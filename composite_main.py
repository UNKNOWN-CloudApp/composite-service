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

USER_SERVICE_URL = os.environ.get("USER_SERVICE_URL", "http://34.27.64.57:8000/")
LISTING_SERVICE_URL = os.environ.get("LISTING_SERVICE_URL", "http://34.134.23.74:8000")
BOOKING_SERVICE_URL = os.environ.get("BOOKING_SERVICE_URL", "https://fastapi-1038095584126.europe-west1.run.app")

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


# MOCK DATA 

USE_MOCK = True  # True

MOCK_LISTINGS = [
    {
        "id": 1,
        "landlord_email": "owner1@example.com",
        "name": "Cozy Studio",
        "address": "Brooklyn NY",
        "start_date": "2026-01-01T00:00:00",
        "end_date": "2026-12-31T23:59:59",
        "description": "A cozy studio near the park."
    },
    {
        "id": 2,
        "landlord_email": "owner2@example.com",
        "name": "Modern Loft",
        "address": "Manhattan NY",
        "start_date": "2026-03-01T00:00:00",
        "end_date": "2026-05-01T09:00:00",
        "description": "AC Available."
    },
    {
        "id": 3,
        "landlord_email": "david.lee@example.com",
        "name": "House",
        "address": "Queens",
        "start_date": "2026-01-01T00:00:00",
        "end_date": "2026-05-01T00:00:00",
        "description": "User owns this house."
    },
]

MOCK_BOOKINGS = [
    {
        "id": 10,
        "listing_id": 1,
        "tenant_email": "someone@example.com",
        "start_date": "2026-01-10T10:00:00",
        "end_date": "2026-01-20T10:00:00"
    },
    {
        "id": 11,
        "listing_id": 2,
        "tenant_email": "eva@example.com",
        "start_date": "2026-05-01T09:00:00",
        "end_date": "2026-05-01T09:00:00"
    }
]

def parse_iso_datetime(s: str) -> datetime:
    try:
        if len(s) == 10:
            return datetime.fromisoformat(s + "T00:00:00")
        return datetime.fromisoformat(s)
    except Exception:
        raise HTTPException(status_code=400, detail=f"Invalid datetime format: {s}")

def ranges_overlap(start1: datetime, end1: Optional[datetime],
                   start2: datetime, end2: Optional[datetime]) -> bool:
    if end1 is None:
        end1 = datetime.max
    if end2 is None:
        end2 = datetime.max
    return not (end1 <= start2 or end2 <= start1)

def fetch_listings(result: Dict[str, Any], keyword: Optional[str]) -> None:
    if USE_MOCK:
        listings = MOCK_LISTINGS.copy()

        # keyword filtering (address or description)
        if keyword:
            keyword_lower = keyword.lower()
            listings = [
                lst for lst in listings
                if keyword_lower in lst["address"].lower() or
                   keyword_lower in lst["description"].lower()
            ]

        result["listings"] = listings
        return

    # ---- real version ----
    try:
        params = {"keyword": keyword} if keyword else {}
        resp = requests.get(f"{LISTING_SERVICE_URL}/listings", params=params, timeout=5)
        resp.raise_for_status()
        result["listings"] = resp.json()
    except Exception as e:
        result["listings_error"] = str(e)

        

def fetch_bookings(result: dict, start_dt: datetime, end_dt: datetime):
    if USE_MOCK:
        overlapping = []
        for b in MOCK_BOOKINGS:
            b_start = parse_iso_datetime(b["start_date"])
            b_end = parse_iso_datetime(b["end_date"]) if b["end_date"] else None

            if ranges_overlap(b_start, b_end, start_dt, end_dt):
                overlapping.append(b)

        result["bookings"] = overlapping
        return

    # ---- real version ----
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
    start: str = Query(..., description="Desired start date (YYYY-MM-DD or ISO datetime)"),
    end: str = Query(..., description="Desired end date (YYYY-MM-DD or ISO datetime)"),
    user_email: str = Query(..., description="Email of the current logged-in user"),
    keyword: Optional[str] = Query(None, description="Keyword for listing search"),
):
    """Return listings that:
       1) Do not belong to the current user
       2) Listing's own date range overlaps with desired range
       3) Do not have an existing overlapping booking
       Listing-service and booking-service are called in parallel threads.
    """

    start_dt = parse_iso_datetime(start)
    end_dt = parse_iso_datetime(end)
    if end_dt <= start_dt:
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
# Endpoint 2: Search listings by keyword (excluding user's own listings)
# -------------------------------------------------------------------

@app.get("/composite/keyword-search-listings")
def keyword_search_listings(
    user_email: str = Query(..., description="Email of current logged-in user"),
    keyword: Optional[str] = Query(None, description="Keyword for search"),
):
    """Return listings filtered by keyword, excluding those owned by the current user."""
      
    result: Dict[str, Any] = {}
    fetch_listings(result, keyword)

    if "listings_error" in result:
        raise HTTPException(
            status_code=502,
            detail=f"Listing service error: {result['listings_error']}",
        )

    listings = result.get("listings", [])

    filtered = [
        l for l in listings
        if l.get("landlord_email") != user_email
    ]

    return filtered


# -------------------------------------------------------------------
# Root & Entrypoint
# -------------------------------------------------------------------

@app.get("/")
def root():
    return {"message": "Composite Service"}


if __name__ == "__main__":
    uvicorn.run("composite_main:app", host="0.0.0.0", port=port, reload=True)