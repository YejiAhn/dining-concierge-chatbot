# Dining Concierge Chatbot

Cloud Computing and Big Data — Fall 2026, Assignment 1

A serverless chatbot that collects dining preferences and emails restaurant suggestions in Manhattan.

## Repository Structure

- `frontend/`: Frontend HTML, CSS, JavaScript, and API SDK files.
- `lambda-functions/`: Final LF0, LF1, and LF2 source code.
- `other-scripts/`: Yelp collection and OpenSearch upload scripts.

## Architecture

S3 Frontend → API Gateway → LF0 → Amazon Lex → LF1 → SQS

EventBridge Scheduler → LF2 → OpenSearch + DynamoDB → Amazon SES

- **LF0:** Sends user messages to Lex and returns chatbot responses.
- **LF1:** Validates dining preferences, checks previous recommendations, and submits requests to SQS.
- **LF2:** Processes requests, selects or reuses restaurants, fetches details, sends recommendation emails, and saves the last recommendation.

## Features

- GreetingIntent, ThankYouIntent, and DiningSuggestionsIntent.
- Collects location, cuisine, party size, dining date/time, and email.
- Validates Manhattan location, supported cuisine, positive integer party size, future dining date/time, and email format.
- Uses America/New_York for date/time validation.
- Processes one SQS request per invocation, every minute.
- Deletes each SQS message after SES accepts the email and recommendation history is saved.

## Restaurant Data

Collected **1,543 unique Manhattan restaurants** using the Yelp API. Restaurants are deduplicated by Yelp business ID, and coordinates are checked against the Manhattan borough boundary.

| Cuisine | Count |
| --- | ---: |
| Chinese | 275 |
| Japanese | 279 |
| Italian | 254 |
| Indian | 250 |
| Mexican | 250 |
| American | 257 |

Restaurants may belong to multiple cuisines, so counts overlap. The chatbot currently offers the first five cuisines.

### DynamoDB

- **`yelp-restaurants`**, keyed by `businessId`: stores name, address, coordinates, reviewCount, rating, zipCode, cuisines, and insertedAtTimestamp.
- **`dining-user-state`**, keyed by `userId`: stores the last location, cuisine, restaurantIds, updatedAt, and requestId.

### OpenSearch

Index: `restaurants`

- `RestaurantID`: keyword
- `Cuisine`: keyword array

The Yelp business ID is used as the document ID to prevent duplicate documents during repeated uploads.

## Extra Credit: Previous Recommendation Memory

- Persists a browser-specific user ID in localStorage and uses it as the Lex session ID and recommendation-history key.
- After successful email submission to SES, stores the last location, cuisine, and recommended restaurant IDs in DynamoDB.
- When a returning user requests the same location and cuisine, asks whether to reuse the previous recommendations through the optional ReusePrevious slot.
- **Yes:** Sends the same restaurants with the current party size, dining date/time, and email address.
- **No:** Generates new random recommendations and updates the history. Random selections can overlap with previous results.
- A different location/cuisine combination does not trigger the reuse question; the application currently supports only Manhattan.

History is scoped to the same browser and website origin. Clearing localStorage or using another browser creates a different user identity.

## Configuration

AWS backend region: `us-east-1`.

### Lambda Environment Variables

| Function | Variables |
| --- | --- |
| LF0 | `LEX_BOT_ID`, `LEX_BOT_ALIAS_ID`, `LEX_LOCALE_ID` |
| LF1 | `QUEUE_URL`, `STATE_TABLE` |
| LF2 | `QUEUE_URL`, `TABLE_NAME`, `STATE_TABLE`, `OPENSEARCH_ENDPOINT`, `OPENSEARCH_USERNAME`, `OPENSEARCH_PASSWORD`, `SES_FROM_EMAIL` |

- `TABLE_NAME`: `yelp-restaurants`
- `STATE_TABLE`: `dining-user-state`
- `QUEUE_URL`: The dining request queue URL, shared by LF1 and LF2.
- `OPENSEARCH_ENDPOINT`: The domain endpoint, not the Dashboards URL.
- `SES_FROM_EMAIL`: A verified SES sender address.

LF2 timeout: **60 seconds**. Message visibility timeout used by LF2: **180 seconds**.

EventBridge Scheduler:
- Expression: `rate(1 minute)`
- Flexible time window: Off
- Target: LF2
- Input: `{}`

Execution roles grant the required SQS, DynamoDB, SES, and CloudWatch permissions. OpenSearch uses HTTPS and fine-grained access control with username/password authentication.

### OpenSearch Upload

Run with an AWS identity that has DynamoDB Scan permission:

```bash
python3 -m pip install boto3 opensearch-py
python3 other-scripts/upload_opensearch.py
```

Enter the OpenSearch domain endpoint, username, and password when prompted. The script verifies total document and cuisine counts against DynamoDB.

## Testing

1. Open the S3-hosted frontend and test greeting and thank-you messages.
2. Complete a dining request with a future date/time and a verified SES email address.
3. Wait for the scheduled worker and check the inbox or spam folder for three restaurant suggestions.
4. Check LF2 CloudWatch logs and the user's record in `dining-user-state`.
5. Close the tab, reopen the same website, and submit the same location and cuisine.
6. Select Yes and confirm that the email contains the same three restaurants with the current dining details.
7. Select No on a subsequent matching request to test new random recommendations.
8. Request a different cuisine to test recommendation generation without the reuse question.

Verified:
- 1,543 documents indexed in OpenSearch, with all cuisine counts matching DynamoDB.
- Manual and scheduled end-to-end recommendation email delivery.
- Recommendation history saved in DynamoDB.
- Returning-user reuse prompt and delivery of the same restaurants after selecting Yes.

## Notes

- SES is in Sandbox mode; sender and recipient addresses must be verified in `us-east-1`.
- Modern OpenSearch does not support the assignment's legacy custom mapping type `Restaurant`. Documents are stored directly in the `restaurants` index.
- Recommendations filter by cuisine. Party size and dining date/time are included in the email but do not check reservation availability.
- Failed requests remain in SQS for retry. Duplicate emails are possible if saving history or deleting the message fails after sending, or SQS redelivers a request.
- API keys and passwords must not be committed to Git or included in the submission ZIP.
- Delete the OpenSearch domain after grading and demonstrations are complete to avoid ongoing charges.
