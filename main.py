from __future__ import annotations

import os
import threading
from typing import Any, Dict, List, Optional
from datetime import datetime, date

import requests
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import uvicorn
from models.booking import BookingCreate, BookingRead, BookingUpdate


# Service URLs
USER_SERVICE_URL = os.environ.get("USER_SERVICE_URL", "http://34.27.64.57:8080/")
LISTING_SERVICE_URL = os.environ.get("LISTING_SERVICE_URL", "http://34.134.23.74:8080/")
BOOKING_SERVICE_URL = os.environ.get("BOOKING_SERVICE_URL", "https://booking-service-1038095584126.us-central1.run.app")

port = 8080

app = FastAPI(title="Composite Service", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Response models
class PaginatedLinks(BaseModel):
    self: str
    next: Optional[str] = None
    prev: Optional[str] = None


class ListingWithLinks(BaseModel):
    data: Dict[str, Any]
    _links: Dict[str, str]


class PaginatedResponse(BaseModel):
    items: List[ListingWithLinks]
    total_items: int
    page: int
    page_size: int
    total_pages: int
    _links: PaginatedLinks

def assert_user_exists(email: str):
    try:
        r = requests.get(
            f"{USER_SERVICE_URL.rstrip('/')}/users/exists",
            params={"email": email},
            timeout=5,
        )
    except requests.RequestException as e:
        raise HTTPException(502, f"User service unreachable: {e}")

    if r.status_code != 200:
        raise HTTPException(502, f"User service error ({r.status_code}): {r.text}")

    data = r.json()
    if not data.get("exists"):
        raise HTTPException(400, f"Invalid user_email (no such user): {email}")

def assert_listing_exists(listing_id: int):
    try:
        r = requests.get(
            f"{LISTING_SERVICE_URL.rstrip('/')}/listing/{listing_id}",
            timeout=5,
        )
    except requests.RequestException as e:
        raise HTTPException(502, f"Listing service unreachable: {e}")

    if r.status_code == 404:
        raise HTTPException(400, f"Invalid listing_id (no such listing): {listing_id}")

    if r.status_code != 200:
        raise HTTPException(502, f"Listing service error ({r.status_code}): {r.text}")

def to_date(date_input: str | date) -> date:
    """Convert date string or date object to date object."""
    if isinstance(date_input, date):
        return date_input
    
    try:
        if len(date_input) == 10:  # YYYY-MM-DD
            return datetime.fromisoformat(date_input).date()
        return datetime.fromisoformat(date_input).date()
    except Exception:
        raise HTTPException(400, f"Invalid date format: {date_input}")


def dates_overlap(start1: Optional[date], end1: Optional[date], 
                  start2: Optional[date], end2: Optional[date]) -> bool:
    """Check if two date ranges overlap."""
    # No filter = include everything
    if start1 is None and end1 is None:
        return True
    
    # Only start1 given
    if start1 and not end1:
        return end2 is None or end2 >= start1
    
    # Only end1 given
    if end1 and not start1:
        return start2 is None or start2 <= end1
    
    # Both given - check for overlap
    if end2 is None:
        return start2 <= end1
    return not (end2 < start1 or start2 > end1)


def get_listings(filters: dict) -> List[dict]:
    """Fetch listings from listing service."""
    resp = requests.get(f"{LISTING_SERVICE_URL}/listing", params=filters, timeout=5)
    resp.raise_for_status()
    data = resp.json()
    
    # Handle paginated response
    if isinstance(data, dict) and "items" in data:
        return [item["data"] for item in data["items"]]
    return data


def get_bookings() -> List[dict]:
    """Fetch all bookings from booking service."""
    resp = requests.get(f"{BOOKING_SERVICE_URL}/bookings/all", timeout=5)
    resp.raise_for_status()
    return resp.json()


@app.get("/composite/available-listings", response_model=PaginatedResponse)
def available_listings(
    # Required
    user_email: str = Query(..., description="Current user's email"),
    
    # Listing filters
    landlord_email: Optional[str] = Query(None),
    name: Optional[str] = Query(None),
    address: Optional[str] = Query(None),
    description: Optional[str] = Query(None),
    
    # Date filters - now accepts date objects
    start: Optional[date] = Query(None),
    end: Optional[date] = Query(None),
    
    # Pagination
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=100),
    
    request: Request = None,
):
    """
    Get available listings that:
    - Match the filters
    - Are available during the date range
    - Don't belong to the current user (unless filtered)
    - Aren't already booked
    """
    assert_user_exists(user_email)
    
    # Validate dates
    if start and end and end <= start:
        raise HTTPException(400, "End date must be after start date")
    
    # Fetch data in parallel
    results = {}
    
    def fetch_listings_thread():
        try:
            filters = {k: v for k, v in {
                "landlord_email": landlord_email,
                "name": name,
                "address": address,
                "description": description
            }.items() if v}
            results["listings"] = get_listings(filters)
        except Exception as e:
            results["listings_error"] = str(e)
    
    def fetch_bookings_thread():
        try:
            all_bookings = get_bookings()
            # Keep only bookings that overlap with requested dates
            overlapping = []
            for b in all_bookings:
                b_start = to_date(b["start_date"])
                b_end = to_date(b["end_date"]) if b.get("end_date") else None
                if dates_overlap(start, end, b_start, b_end):
                    overlapping.append(b)
            results["bookings"] = overlapping
        except Exception as e:
            results["bookings_error"] = str(e)
    
    # Start both threads
    t1 = threading.Thread(target=fetch_listings_thread)
    t2 = threading.Thread(target=fetch_bookings_thread)
    t1.start()
    t2.start()
    t1.join()
    t2.join()
    
    # Check for errors
    if "listings_error" in results:
        raise HTTPException(502, f"Listing service error: {results['listings_error']}")
    if "bookings_error" in results:
        raise HTTPException(502, f"Booking service error: {results['bookings_error']}")
    
    # Filter available listings
    booked_ids = {b["listing_id"] for b in results["bookings"]}

    available = []
    
    for lst in results["listings"]:
        # Skip user's own listings (unless explicitly filtered)
        if not landlord_email and lst.get("landlord_email") == user_email:
            continue
        
        # Check date overlap
        lst_start = to_date(lst["start_date"]) if lst.get("start_date") else None
        lst_end = to_date(lst["end_date"]) if lst.get("end_date") else None
        if not dates_overlap(start, end, lst_start, lst_end):
            continue
        
        # Skip booked listings
        if lst["id"] in booked_ids:
            continue
        
        available.append(lst)
    
    # Paginate
    total = len(available)
    total_pages = (total + page_size - 1) // page_size
    
    if page > total_pages and total > 0:
        raise HTTPException(404, f"Page {page} doesn't exist. Total pages: {total_pages}")
    
    start_idx = (page - 1) * page_size
    page_items = available[start_idx:start_idx + page_size]
    
    # Build response with links
    items = [
        ListingWithLinks(
            data=lst,
            _links={
                "self": f"/listing/{lst['id']}",
                "landlord_listings": f"/listing/user/{lst.get('landlord_email')}",
            }
        )
        for lst in page_items
    ]
    
    # Build pagination links
    params = [f"user_email={user_email}"]
    if landlord_email:
        params.append(f"landlord_email={landlord_email}")
    if name:
        params.append(f"name={name}")
    if address:
        params.append(f"address={address}")
    if description:
        params.append(f"description={description}")
    if start:
        params.append(f"start={start}")
    if end:
        params.append(f"end={end}")
    
    query = "&".join(params)
    base = str(request.url.path)
    
    return PaginatedResponse(
        items=items,
        total_items=total,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
        _links=PaginatedLinks(
            self=f"{base}?{query}&page={page}&page_size={page_size}",
            next=f"{base}?{query}&page={page+1}&page_size={page_size}" if page < total_pages else None,
            prev=f"{base}?{query}&page={page-1}&page_size={page_size}" if page > 1 else None,
        ),
    )

@app.post("/composite/create-bookings", status_code=201, response_model=BookingRead)
def create_booking_composite(payload: BookingCreate):
    # logical FK constraint
    assert_listing_exists(payload.listing_id)

    try:
        r = requests.post(
            f"{BOOKING_SERVICE_URL.rstrip('/')}/bookings",
            json=payload.model_dump(mode="json"),
            timeout=10,
        )
    except requests.RequestException as e:
        raise HTTPException(502, f"Booking service unreachable: {e}")

    if r.status_code >= 400:
        raise HTTPException(r.status_code, f"Booking service error: {r.text}")

    return r.json()

@app.get("/")
def root():
    return {"message": "Composite Service"}


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=True)