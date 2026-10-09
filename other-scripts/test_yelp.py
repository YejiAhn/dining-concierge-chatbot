import getpass
import json
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


def main():
    api_key = getpass.getpass("Yelp API key: ").strip()

    params = urlencode({
        "term": "Japanese restaurants",
        "location": "Manhattan, NY",
        "categories": "japanese",
        "limit": 5
    })

    request = Request(
        f"https://api.yelp.com/v3/businesses/search?{params}",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/json"
        }
    )

    try:
        with urlopen(request, timeout=20) as response:
            data = json.load(response)
    except HTTPError as error:
        print(f"HTTP error: {error.code}")
        print(error.read().decode())
        return

    print("Total search matches:", data.get("total"))
    print(json.dumps(
        data.get("businesses", []),
        indent=2,
        ensure_ascii=False
    ))


if __name__ == "__main__":
    main()
