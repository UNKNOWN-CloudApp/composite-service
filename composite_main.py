from __future__ import annotations

import os
import socket
import threading
import time
from typing import Any, Dict, List, Optional
from pydantic import BaseModel

import requests
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from datetime import date, datetime

import uvicorn


#   USER_SERVICE_URL=http://user-service:8000
#   LISTING_SERVICE_URL=http://listing-service:8001
#   BOOKING_SERVICE_URL=http://booking-service:8002

USER_SERVICE_URL = os.environ.get("USER_SERVICE_URL", "http://34.27.64.57:8080/")
LISTING_SERVICE_URL = os.environ.get("LISTING_SERVICE_URL", "http://34.134.23.74:8080")
BOOKING_SERVICE_URL = os.environ.get("BOOKING_SERVICE_URL", "https://booking-service-1038095584126.us-central1.run.app")

COMPOSITE_PORT = int(os.environ.get("FASTAPIPORT", 8080))
port = 8080
hostname = socket.gethostname()


app = FastAPI(
    title="Composite Service",
    description="Composite microservice for listing + booking aggregation",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

def parse_iso_datetime(s: str) -> datetime:
    try:
        if len(s) == 10:
            return datetime.fromisoformat(s + "T00:00:00")
        return datetime.fromisoformat(s)
    except Exception:
        raise HTTPException(status_code=400, detail=f"Invalid datetime format: {s}")

def ranges_overlap(req_start, req_end, listing_start, listing_end):
    # If user gave no date filters → nothing is excluded
    if req_start is None and req_end is None:
        return True  # treat as no filtering

    # If only start given → check listing_end >= req_start
    if req_start is not None and req_end is None:
        return listing_end is None or listing_end >= req_start

    # If only end given → check listing_start <= req_end
    if req_start is None and req_end is not None:
        return listing_start <= req_end

    # If both given:
    return not (listing_end < req_start or listing_start > req_end)


def fetch_listings(result: Dict[str, Any], keyword: Optional[str]) -> None:

    try:
        params = {"keyword": keyword} if keyword else {}
        resp = requests.get(f"{LISTING_SERVICE_URL}/listing", params=params, timeout=5)
        resp.raise_for_status()
        result["listings"] = resp.json()
    except Exception as e:
        result["listings_error"] = str(e)
        

def fetch_bookings(result: dict, start_dt: datetime, end_dt: datetime):

    try:
        resp = requests.get(f"{BOOKING_SERVICE_URL}/bookings/all", timeout=5)
        resp.raise_for_status()
        all_bookings = resp.json()

        overlapping = []
        for b in all_bookings:
            b_start = parse_iso_datetime(b["start_date"])
            b_end = parse_iso_datetime(b["end_date"]) if b["end_date"] else None

            if ranges_overlap(b_start, b_end, start_dt, end_dt):
                overlapping.append(b)

        result["bookings"] = overlapping

    except Exception as e:
        result["bookings_error"] = str(e)


# -------------------------------------------------------------------
# Endpoint 1: Find available listings
# -------------------------------------------------------------------

@app.get("/composite/available-listings")
def list_available_listings(
    start: Optional[str] = Query(None, description="Desired start date (YYYY-MM-DD or ISO datetime)"),
    end: Optional[str] = Query(None, description="Desired end date (YYYY-MM-DD or ISO datetime)"),
    user_email: str = Query(..., description="Email of the current logged-in user"),
    keyword: Optional[str] = Query(None, description="Keyword for listing search"),
):
    """Return listings that:
       1) Do not belong to the current user
       2) Listing's own date range overlaps with desired range
       3) Do not have an existing overlapping booking
       Listing-service and booking-service are called in parallel threads.
    """
    if start:
        start_dt = parse_iso_datetime(start)
    if end:
        end_dt = parse_iso_datetime(end)
    if start_dt and end_dt and end_dt <= start_dt:
        raise HTTPException(status_code=400, detail="End date must be after start date")

    shared: Dict[str, Any] = {}

    t1 = threading.Thread(target=fetch_listings, args=(shared, keyword))
    t2 = threading.Thread(target=fetch_bookings, args=(shared, start_dt, end_dt))

    t1.start()
    t2.start()
    t1.join()
    t2.join()

    # Errors
    if "listings_error" in shared:
        raise HTTPException(status_code=502, detail=f"Listing service error: {shared['listings_error']}")

    if "bookings_error" in shared:
        raise HTTPException(status_code=502, detail=f"Booking service error: {shared['bookings_error']}")

    listings: List[Dict[str, Any]] = shared.get("listings", [])
    bookings: List[Dict[str, Any]] = shared.get("bookings", [])

    # Listing IDs that already have overlapping bookings
    booked_listing_ids = {b["listing_id"] for b in bookings}

    available: List[Dict[str, Any]] = []

    for lst in listings:
        landlord = lst.get("landlord_email")

        if landlord == user_email:
            continue

        lst_start = parse_iso_datetime(lst["start_date"]) if lst.get("start_date") else None
        lst_end = parse_iso_datetime(lst["end_date"]) if lst.get("end_date") else None

        if lst_start and not ranges_overlap(lst_start, lst_end, start_dt, end_dt):
            continue

        if lst["id"] in booked_listing_ids: 
            continue

        available.append(lst)

    return {"available_listings": available}





# -------------------------------------------------------------------
# Root & Entrypoint
# -------------------------------------------------------------------

@app.get("/")
def root():
    return {"message": "Composite Service"}


if __name__ == "__main__":
    uvicorn.run("composite_main:app", host="0.0.0.0", port=port, reload=True)