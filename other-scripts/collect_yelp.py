import getpass
import json
import time
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import boto3
from shapely.geometry import Point, shape
from shapely.prepared import prep


REGION = "us-east-1"
TABLE_NAME = "yelp-restaurants"

TARGET_PER_CUISINE = 250
MAX_REQUESTS = 250
CHECKPOINT = Path("restaurants.json")

CUISINES = {
    "chinese": "chinese",
    "japanese": "japanese",
    "italian": "italian",
    "indian": "indpak",
    "mexican": "mexican",
    "american": "tradamerican,newamerican",
}

SEARCH_LOCATIONS = [
    "Manhattan, NY",
    "Chinatown, Manhattan, NY",
    "East Village, Manhattan, NY",
    "Chelsea, Manhattan, NY",
    "Midtown, Manhattan, NY",
    "Upper East Side, Manhattan, NY",
    "Upper West Side, Manhattan, NY",
    "Harlem, Manhattan, NY",
    "Washington Heights, Manhattan, NY",
]


def save_checkpoint(restaurants):
    temporary = CHECKPOINT.with_suffix(".tmp")
    with temporary.open("w") as file:
        json.dump(
            list(restaurants.values()),
            file,
            indent=2,
            ensure_ascii=False,
            default=float,
        )
    temporary.replace(CHECKPOINT)


def load_checkpoint():
    if not CHECKPOINT.exists():
        return {}

    with CHECKPOINT.open() as file:
        items = json.load(file, parse_float=Decimal)

    return {item["businessId"]: item for item in items}


def load_manhattan_boundary():
    url = (
        "https://data.cityofnewyork.us/resource/"
        "gthc-hcne.json?boroname=Manhattan"
    )
    with urlopen(url, timeout=30) as response:
        rows = json.load(response)

    if len(rows) != 1 or rows[0]["boroname"] != "Manhattan":
        raise RuntimeError("Unable to load Manhattan boundary.")

    return prep(shape(rows[0]["the_geom"]))


def is_in_manhattan(business, boundary):
    coordinates = business.get("coordinates") or {}
    latitude = coordinates.get("latitude")
    longitude = coordinates.get("longitude")

    if latitude is None or longitude is None:
        return False

    # GeoJSON and Shapely use longitude, latitude order.
    point = Point(float(longitude), float(latitude))
    return boundary.covers(point)


def cuisine_count(restaurants, cuisine):
    return sum(
        cuisine in item["cuisines"]
        for item in restaurants.values()
    )


def search_yelp(api_key, category, location, offset, limit):
    params = urlencode({
        "term": "restaurants",
        "location": location,
        "categories": category,
        "sort_by": "best_match",
        "limit": limit,
        "offset": offset,
    })

    request = Request(
        f"https://api.yelp.com/v3/businesses/search?{params}",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/json",
        },
    )

    with urlopen(request, timeout=30) as response:
        return json.load(response, parse_float=Decimal)


def merge_restaurant(restaurants, business, cuisine):
    business_id = business["id"]

    if business_id in restaurants:
        item = restaurants[business_id]
        item["cuisines"] = sorted(
            set(item["cuisines"]) | {cuisine}
        )
        return

    location = business.get("location") or {}

    restaurants[business_id] = {
        "businessId": business_id,
        "name": business["name"],
        "address": ", ".join(
            location.get("display_address", [])
        ),
        "coordinates": business["coordinates"],
        "reviewCount": business.get("review_count", 0),
        "rating": business.get("rating", 0),
        "zipCode": location.get("zip_code", ""),
        "cuisines": [cuisine],
        "insertedAtTimestamp": datetime.now(
            timezone.utc
        ).isoformat(),
    }


def main():
    api_key = getpass.getpass("Yelp API key: ").strip()
    if not api_key:
        raise ValueError("Yelp API key is required.")

    boundary = load_manhattan_boundary()
    restaurants = load_checkpoint()
    print(f"Loaded {len(restaurants)} checkpoint items.")

    requests_made = 0

    for cuisine, category in CUISINES.items():
        for location in SEARCH_LOCATIONS:
            if cuisine_count(restaurants, cuisine) >= TARGET_PER_CUISINE:
                break

            if requests_made >= MAX_REQUESTS:
                break

            # At most 240 results for each search condition.
            for offset in range(0, 240, 50):
                if cuisine_count(restaurants, cuisine) >= TARGET_PER_CUISINE:
                    break

                if requests_made >= MAX_REQUESTS:
                    break

                limit = min(50, 240 - offset)
                requests_made += 1

                try:
                    data = search_yelp(
                        api_key, category, location, offset, limit
                    )
                except HTTPError as error:
                    save_checkpoint(restaurants)
                    print(f"\nYelp HTTP error: {error.code}")
                    print(error.read().decode())
                    print("Collected data is saved in restaurants.json.")
                    raise SystemExit(1)

                businesses = data.get("businesses", [])

                for business in businesses:
                    if (
                        not business.get("is_closed", False)
                        and is_in_manhattan(business, boundary)
                    ):
                        merge_restaurant(
                            restaurants, business, cuisine
                        )

                save_checkpoint(restaurants)

                print(
                    f"{cuisine} | {location} | offset={offset} | "
                    f"cuisine total={cuisine_count(restaurants, cuisine)} | "
                    f"unique total={len(restaurants)}"
                )

                time.sleep(0.5)

                if len(businesses) < limit:
                    break

    counts = {
        cuisine: cuisine_count(restaurants, cuisine)
        for cuisine in CUISINES
    }

    print("\n=== Collection summary ===")
    print(f"API requests this run: {requests_made}")
    print(f"Unique Manhattan restaurants: {len(restaurants)}")
    for cuisine, count in counts.items():
        print(f"{cuisine}: {count}")

    if not restaurants:
        raise RuntimeError("No restaurants collected; nothing to upload.")

    table = boto3.resource(
        "dynamodb", region_name=REGION
    ).Table(TABLE_NAME)

    print("\nUploading to DynamoDB...")
    with table.batch_writer() as batch:
        for item in restaurants.values():
            batch.put_item(Item=item)

    print(f"Uploaded {len(restaurants)} unique restaurants.")

    enough_total = len(restaurants) >= 1000
    enough_cuisines = sum(count >= 200 for count in counts.values()) >= 5

    if enough_total and enough_cuisines:
        print("PASS: 1,000+ unique restaurants and 5+ cuisines with 200+ each.")
    else:
        print("INCOMPLETE: More collection is needed; review the summary.")


if __name__ == "__main__":
    main()
