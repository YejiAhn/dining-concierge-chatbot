import getpass
from collections import Counter
from urllib.parse import urlparse

import boto3
from opensearchpy import OpenSearch, helpers


REGION = "us-east-1"
TABLE_NAME = "yelp-restaurants"
INDEX_NAME = "restaurants"


def main():
    endpoint = input("OpenSearch domain endpoint: ").strip()
    username = input("Master username: ").strip()
    password = getpass.getpass("Master password: ")

    if not endpoint.startswith("https://"):
        endpoint = "https://" + endpoint

    parsed = urlparse(endpoint)
    if not parsed.hostname or parsed.path not in ("", "/"):
        raise ValueError(
            "Use the domain endpoint, not the Dashboards URL."
        )

    client = OpenSearch(
        hosts=[{
            "host": parsed.hostname,
            "port": 443,
        }],
        http_auth=(username, password),
        use_ssl=True,
        verify_certs=True,
        timeout=60,
    )

    # Check connection and the existing index before uploading.
    client.info()
    if not client.indices.exists(index=INDEX_NAME):
        raise RuntimeError("Create the restaurants index first.")

    table = boto3.resource(
        "dynamodb", region_name=REGION
    ).Table(TABLE_NAME)

    # Read every page, not only the first Scan response.
    items = []
    scan_args = {
        "ProjectionExpression": "businessId, cuisines",
        "ConsistentRead": True,
    }

    while True:
        response = table.scan(**scan_args)
        items.extend(response.get("Items", []))

        last_key = response.get("LastEvaluatedKey")
        if not last_key:
            break

        scan_args["ExclusiveStartKey"] = last_key

    print(f"Read {len(items)} items from DynamoDB.")

    # Validate all records before starting the upload.
    documents = {}
    cuisine_counts = Counter()

    for item in items:
        business_id = item.get("businessId")
        cuisines = item.get("cuisines")

        if (
            not isinstance(business_id, str)
            or not business_id
            or not isinstance(cuisines, list)
            or not cuisines
            or any(
                not isinstance(cuisine, str) or not cuisine.strip()
                for cuisine in cuisines
            )
        ):
            raise ValueError(f"Invalid restaurant data: {item}")

        if business_id in documents:
            raise ValueError(f"Duplicate businessId: {business_id}")

        cuisines = sorted({
            cuisine.strip().lower() for cuisine in cuisines
        })

        documents[business_id] = {
            "RestaurantID": business_id,
            "Cuisine": cuisines,
        }
        cuisine_counts.update(cuisines)

    if not documents:
        raise RuntimeError("No restaurants found; nothing to upload.")

    actions = (
        {
            "_op_type": "index",
            "_index": INDEX_NAME,
            "_id": business_id,
            "_source": document,
        }
        for business_id, document in documents.items()
    )

    print("Uploading to OpenSearch...")

    uploaded, _ = helpers.bulk(
        client,
        actions,
        chunk_size=200,
        max_retries=3,
        raise_on_error=True,
        raise_on_exception=True,
    )

    # Make the uploaded documents searchable before checking counts.
    client.indices.refresh(index=INDEX_NAME)

    total = client.count(index=INDEX_NAME)["count"]
    print(f"Uploaded {uploaded} restaurants.")
    print(f"OpenSearch document count: {total}")

    if total != len(documents):
        raise RuntimeError(
            "Document count mismatch. Check for old/test documents."
        )

    for cuisine, expected in sorted(cuisine_counts.items()):
        actual = client.count(
            index=INDEX_NAME,
            body={"query": {"term": {"Cuisine": cuisine}}},
        )["count"]

        print(f"{cuisine}: {actual} (expected {expected})")

        if actual != expected:
            raise RuntimeError(f"Cuisine count mismatch: {cuisine}")

    print("PASS: Total and cuisine counts match DynamoDB.")


if __name__ == "__main__":
    main()
