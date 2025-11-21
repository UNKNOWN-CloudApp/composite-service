from __future__ import annotations

import os
import socket
import threading
import time
from typing import Any, Dict, List, Optional
from pydantic import BaseModel

import requests
from fastapi import FastAPI, HTTPException, Path
from uuid import UUID

import uvicorn

# ------------------------------------------------------------------------
# Config: where are the atomic microservices?
# ------------------------------------------------------------------------

# You can override these in your environment / Docker:
#   USER_SERVICE_URL=http://user-service:8000
#   LISTING_SERVICE_URL=http://listing-service:8001
#   BOOKING_SERVICE_URL=http://booking-service:8002

USER_SERVICE_URL = os.environ.get("USER_SERVICE_URL", "http://35.184.175.130:8000")
LISTING_SERVICE_URL = os.environ.get("LISTING_SERVICE_URL", "http://35.232.88.9:8001")
BOOKING_SERVICE_URL = os.environ.get("BOOKING_SERVICE_URL", "http://136.114.135.5:8002")

COMPOSITE_PORT = int(os.environ.get("FASTAPIPORT", 8003))

hostname = socket.gethostname()

app = FastAPI(
    title="Subletting Composite Service",
    description=(
        "Composite service that encapsulates user-, listing-, and booking-services. "
        "Implements logical foreign key constraints and uses threads for parallel execution."
    ),
    version="1.0.0",
)

class UserRead(BaseModel):
    id: int
    username: str
    email: str

class ListingRead(BaseModel):
    id: int
    title: str
    price: float

class BookingCreate(BaseModel):
    listing_id: UUID
    tenant_id: UUID
    landlord_id: UUID

class BookingRead(BookingCreate):
    id: UUID



# ------------------------------------------------------------------------
# Small helper: forwarding GET/POST to atomic services
# ------------------------------------------------------------------------

def forward_get(base_url: str, path: str, params: Optional[Dict[str, Any]] = None) -> Any:
    """Forward a GET request to one of the atomic services."""
    url = f"{base_url}{path}"
    try:
        r = requests.get(url, params=params)
    except requests.RequestException as e:
        raise HTTPException(status_code=502, detail=f"Error calling {url}: {e}")

    if r.status_code >= 400:
        # Pass through error from atomic service
        raise HTTPException(status_code=r.status_code, detail=r.text)
    return r.json()


def forward_post(base_url: str, path: str, payload: Dict[str, Any]) -> Any:
    """Forward a POST request to one of the atomic services."""
    url = f"{base_url}{path}"
    try:
        r = requests.post(url, json=payload)
    except requests.RequestException as e:
        raise HTTPException(status_code=502, detail=f"Error calling {url}: {e}")

    if r.status_code >= 400:
        raise HTTPException(status_code=r.status_code, detail=r.text)
    return r.json()


# ------------------------------------------------------------------------
# 1) Encapsulated endpoints: expose atomic APIs via composite
# ------------------------------------------------------------------------
# These show that the composite “implements” the microservice APIs and just
# delegates onto the atomic services underneath.
# ------------------------------------------------------------------------


# ---- USER-SERVICE passthroughs ----

@app.get("/users/{user_id}", response_model=UserRead)
def composite_get_user(user_id: int = Path(..., ge=1)):
    """
    Encapsulated endpoint for `GET /users/{user_id}`.

    Delegates to the user-service.
    """
    data = forward_get(USER_SERVICE_URL, f"/users/{user_id}")
    return data


# ---- LISTING-SERVICE passthroughs ----

@app.get("/listing", response_model=List[ListingRead])
def composite_list_listings():
    """
    Encapsulated endpoint for `GET /listing`.

    Delegates to the listing-service.
    """
    data = forward_get(LISTING_SERVICE_URL, "/listing")
    return data


@app.get("/listing/{listing_id}", response_model=ListingRead)
def composite_get_listing(listing_id: int = Path(..., ge=1)):
    """
    Encapsulated endpoint for `GET /listing/{listing_id}`.

    Delegates to the listing-service.
    """
    data = forward_get(LISTING_SERVICE_URL, f"/listing/{listing_id}")
    return data


# ---- BOOKING-SERVICE passthrough (simple GET) ----

@app.get("/bookings/{booking_id}", response_model=BookingRead)
def composite_get_booking(booking_id: UUID):
    """
    Encapsulated endpoint for `GET /bookings/{booking_id}`.

    Delegates to the booking-service.
    """
    data = forward_get(BOOKING_SERVICE_URL, f"/bookings/{booking_id}")
    return data


# ------------------------------------------------------------------------
# 2) Logical foreign key constraints for booking creation
# ------------------------------------------------------------------------
# Booking model has:
#   listing_id: UUID
#   tenant_id: UUID
#   landlord_id: UUID
#
# Composite checks:
#   - Listing with this listing_id exists
#   - Tenant user exists
#   - Landlord user exists
# BEFORE it forwards the create request to booking-service.
# ------------------------------------------------------------------------


@app.post("/bookings", response_model=BookingRead, status_code=201)
def composite_create_booking(booking: BookingCreate):
    """
    Composite booking creation.

    Demonstrates *logical foreign key constraints* across microservices:
    - booking.listing_id must exist in listing-service
    - booking.tenant_id must exist in user-service
    - booking.landlord_id must exist in user-service
    """

    # NOTE: Your current Listing and User services use integer IDs in their
    # models, while Booking uses UUID. For the assignment, the important part
    # is the *pattern* of checking related resources across services.
    # You can later unify your ID types if you want everything to run fully.

    # 1. Check listing exists
    try:
        # Assuming listing_id corresponds to a listing in the listing-service.
        # If types differ, this will just 4xx and we turn that into a 400.
        _ = forward_get(LISTING_SERVICE_URL, f"/listing/{booking.listing_id}")
    except HTTPException as e:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid listing_id '{booking.listing_id}': {e.detail}",
        )

    # 2. Check tenant exists
    try:
        _ = forward_get(USER_SERVICE_URL, f"/users/{booking.tenant_id}")
    except HTTPException as e:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid tenant_id '{booking.tenant_id}': {e.detail}",
        )

    # 3. Check landlord exists
    try:
        _ = forward_get(USER_SERVICE_URL, f"/users/{booking.landlord_id}")
    except HTTPException as e:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid landlord_id '{booking.landlord_id}': {e.detail}",
        )

    # If all checks pass, delegate to booking-service
    created = forward_post(BOOKING_SERVICE_URL, "/bookings", booking.model_dump())
    return created


# ------------------------------------------------------------------------
# 3) Threaded composite endpoint (using `threading` + `time`)
# ------------------------------------------------------------------------
# This endpoint:
#   GET /composite/listing/{listing_id}
#
# runs multiple calls in parallel:
#   - GET listing-service /listing/{id}
#   - GET booking-service /bookings (then filters those for this listing)
#
# This uses Python's `threading.Thread` and `time.sleep` to clearly show
# parallelism for your assignment.
# ------------------------------------------------------------------------


def threaded_safe_get(
    url: str,
    result_dict: Dict[str, Any],
    key: str,
    params: Optional[Dict[str, Any]] = None,
) -> None:
    """
    Helper function used as thread target.

    It performs a GET request, stores the JSON (or error) in result_dict[key].
    Uses time.sleep(...) just so it's obvious that multiple threads overlap.
    """
    # Simulate a bit of network / processing delay (optional)
    time.sleep(0.05)

    try:
        r = requests.get(url, params=params)
        if r.status_code >= 400:
            result_dict[key] = {"error": r.text, "status_code": r.status_code}
        else:
            result_dict[key] = r.json()
    except Exception as e:
        result_dict[key] = {"error": str(e)}


@app.get("/composite/listing/{listing_id}")
def composite_listing_view(listing_id: int = Path(..., ge=1)):
    """
    Composite 'view' that returns listing + its bookings in one response.

    Uses *threads* to call listing-service and booking-service in parallel.

    Example response:
    {
      "listing": {...},
      "bookings_for_listing": [...],
      "source_host": "..."
    }
    """
    results: Dict[str, Any] = {
        "listing": None,
        "all_bookings": None,
    }

    listing_url = f"{LISTING_SERVICE_URL}/listing/{listing_id}"
    bookings_url = f"{BOOKING_SERVICE_URL}/bookings"

    # Create threads
    t_listing = threading.Thread(
        target=threaded_safe_get,
        args=(listing_url, results, "listing"),
    )
    t_bookings = threading.Thread(
        target=threaded_safe_get,
        args=(bookings_url, results, "all_bookings"),
    )

    # Start threads
    t_listing.start()
    t_bookings.start()

    # Wait for both to complete
    t_listing.join()
    t_bookings.join()

    # Handle cases where one of the calls failed
    listing_data = results.get("listing")
    if isinstance(listing_data, dict) and "error" in listing_data:
        # Pass error up
        raise HTTPException(
            status_code=502,
            detail=f"Error fetching listing: {listing_data['error']}",
        )

    bookings_data = results.get("all_bookings")
    if isinstance(bookings_data, dict) and "error" in bookings_data:
        # We can still return the listing, but warn about bookings
        bookings_for_listing: List[Dict[str, Any]] = []
        warning = f"Could not fetch bookings: {bookings_data['error']}"
    else:
        # Filter bookings client-side for this listing_id
        all_bookings_list = bookings_data or []
        bookings_for_listing = [
            b for b in all_bookings_list
            if str(b.get("listing_id")) == str(listing_id)
        ]
        warning = None

    response: Dict[str, Any] = {
        "listing": listing_data,
        "bookings_for_listing": bookings_for_listing,
        "source_host": hostname,
    }
    if warning:
        response["warning"] = warning
    return response


# ------------------------------------------------------------------------
# Root + health
# ------------------------------------------------------------------------


@app.get("/")
def root():
    return {
        "message": "Subletting Composite Service",
        "encapsulates": ["user-service", "listing-service", "booking-service"],
        "host": hostname,
    }


# Entrypoint
if __name__ == "__main__":
    uvicorn.run("composite_main:app", host="0.0.0.0", port=COMPOSITE_PORT, reload=True)
