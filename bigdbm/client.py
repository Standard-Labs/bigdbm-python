"""The Client."""
import requests
from requests import Request, Session
from requests import RequestException

import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from bigdbm.schemas import (
    ConfigDates,
    IABJob,
    IntentEvent,
    UniqueMD5,
    PII,
    MD5WithPII
)
from bigdbm.error import BigDBMApiError


class BigDBMClient:
    """
    Client to interface with BigDBM.

    Creating an instance is fairly expensive, as it automatically retrieves
    an access token and configuration dates.
    """

    def __init__(self, client_id: str, client_secret: str) -> None:
        """Initialize the BigDBM client."""
        self.client_id: str = client_id
        self.client_secret: str = client_secret

        # Access token declarations (defined by _update_token)
        self._access_token: str = ""
        self._access_token_expiration: int = 0  # unix timestamp
        self._update_token()

    def _update_token(self) -> None:
        """Update the token inplace."""
        response = requests.post(
            "https://aws-prod-auth-service.bigdbm.com/oauth2/token",
            headers={
                "Content-Type": "application/x-www-form-urlencoded"
            },
            data={
                "grant_type": "client_credentials",
                "client_id": self.client_id,
                "client_secret": self.client_secret
            }
        )

        response.raise_for_status()
        response_json = response.json()
        
        self._access_token = response_json["access_token"]
        self._access_token_expiration = int(time.time() - 10) + response_json["expires_in"]
        return

    def _access_token_valid(self) -> bool:
        """
        Returns a boolean of whether the current access token is still active.
        For this to be True, the access token must both exist and be before expiration.
        """
        if not self._access_token:
            return False

        if time.time() >= self._access_token_expiration:
            return False

        return True

    def _request(self, request: Request) -> dict:
        """
        Abstracted requesting mechanism handling access token.
        Raises for status automatically. 
        
        Returns a dictionary of the response's JSON.
        """
        if not self._access_token_valid():
            self._update_token()

        # Insert access token into request
        request.headers.update({
            "Authorization": f"Bearer {self._access_token}"
        })

        try:
            with Session() as session:
                response = session.send(request.prepare())
            response.raise_for_status()
        except RequestException:
            # If there's an error, wait and try just once more
            time.sleep(10)
            with Session() as session:
                response = session.send(request.prepare())
            response.raise_for_status()

        return response.json()
    
    def get_config_dates(self) -> ConfigDates:
        """Get the configuration dates from /config."""
        response_json: dict[str, str] = self._request(
            Request(
                method="GET",
                url="https://aws-prod-intent-api.bigdbm.com/intent/configData",
                headers={
                    "Content-Type": "application/x-www-form-urlencoded"
                }
            )
        )

        return ConfigDates(
            start_date=response_json["startDate"],
            end_date=response_json["endDate"]
        )

    def create_job(self, iab_job: IABJob) -> int:
        """
        Creates IAB job and returns an integer of the job number.
        Does not wait for the job to finish.
        """
        config_dates: ConfigDates = self.get_config_dates()

        request = Request(
            method="POST",
            url="https://aws-prod-intent-api.bigdbm.com/intent/createList",
            headers={
                "Content-Type": "application/json"
            },
            json={
                "StartDate": config_dates.start_date,
                "EndDate": config_dates.end_date,
                **iab_job.as_payload()
            }
        )

        return int(self._request(request)["listQueueId"])

    def get_list_status(self, list_queue_id: int) -> int:
        """Get the processing status of a list."""
        request = Request(
            method="GET",
            url="https://aws-prod-intent-api.bigdbm.com/intent/checkList",
            params={"listQueueId": list_queue_id}
        )

        return int(self._request(request)["status"])
    
    def wait_until_completion(self, list_queue_id: int) -> None:
        """Wait until a job has finished processing."""
        while (status := self.get_list_status(list_queue_id)) != 100:
            if status > 100:
                raise BigDBMApiError(f"List ID {list_queue_id} had an error.")
            
            time.sleep(3)

        return

    def create_and_wait(self, iab_job: IABJob) -> int:
        """
        Create an intent data job and wait until it has processed.
        Returns an (int) of the listQueueId for pulling the results.
        """
        list_queue_id: int = self.create_job(iab_job)
        self.wait_until_completion(list_queue_id)
        return list_queue_id
    
    def _fetch_result_response(self, list_queue_id: int, page_num: int) -> dict:
        """Return the JSON API response when pulling a page's results."""
        request = Request(
            method="POST",
            url="https://aws-prod-intent-api.bigdbm.com/intent/result",
            headers={"Content-Type": "application/json"},
            json={"ListQueueId": list_queue_id, "Page": page_num}
        )

        return self._request(request)

    def _extract_intent_events(self, fetch_result_json: dict) -> list[IntentEvent]:
        """Pull the intent events listed on a job's results page."""
        return [
            IntentEvent(
                md5=obj["mD5"],
                sentence=obj["sentence"]
            )
            for obj in fetch_result_json["result"]
        ]

    def retrieve_md5s(self, list_queue_id: int, n_threads: int = 30) -> list[IntentEvent]:
        """Pull all MD5s from an intent job with multithreads."""
        # First page
        response_json: dict = self._fetch_result_response(list_queue_id, 1)
        page_count: int = response_json["totalCount"]
        intent_events: list[IntentEvent] = self._extract_intent_events(response_json)

        def pull_page(p: int) -> list[IntentEvent]:
            return self._extract_intent_events(
                self._fetch_result_response(list_queue_id, p)
            )

        with ThreadPoolExecutor(max_workers=n_threads) as executor:
            result = executor.map(
                pull_page,
                range(2, page_count+1)  # ex. [2, 3, 4] for page_count of 4
            )

        page: list[IntentEvent]
        for page in result:
            intent_events.extend(page)

        return intent_events

    def uniquify_md5s(self, md5s: list[IntentEvent]) -> list[UniqueMD5]:
        """
        Take a list of raw intent events and create unique MD5s 
        each with all sentences.
        """
        md5s_dict: dict[str, list[str]] = {}

        for md5 in md5s:
            if md5.md5 not in md5s_dict:
                md5s_dict[md5.md5] = []

            md5s_dict[md5.md5].append(md5.sentence)

        # Convert to unique model for auto validation
        key: str
        val: list[str]

        return [UniqueMD5(md5=key, sentences=val) for key, val in md5s_dict.items()]

    def check_numbers(self, iab_job: IABJob) -> dict[str, int]:
        """
        Check the availability of data. Specify the max number of hems in the
        IABJob payload. If 1,000 is the n_hems, and there are 10,000 available,
        it'll appear as though only 1,000 are available. But, a 10,000 hem job
        will take far longer. 

        Returns a dictionary with the keys "total" and "unique". All values are
        integers.
        """
        list_queue_id: int = self.create_and_wait(iab_job)
        events: list[IntentEvent] = self.retrieve_md5s(list_queue_id)
        unique_md5s: list[UniqueMD5] = self.uniquify_md5s(events)

        return {
            "total": len(events),
            "unique": len(unique_md5s)
        }

    def _pull_pii(self, md5s: list[str], output_id: int = 10008) -> dict[str, dict[str, Any]]:
        """Retrieve PII for a list of MD5 objects."""
        request = Request(
            method="POST",
            url="https://aws-prod-dataapi-v09.bigdbm.com/GetDataBy/Md5",
            headers={
                "Content-Type": "application/json"
            },
            json={
                "RequestId": "abcdefg",
                "ObjectList": md5s,
                "OutputId": output_id
            }
        )

        # Each dictionary is a single object in a list
        data: dict[str, list[dict[str, Any]]] = self._request(request)["returnData"]

        # Remove the list and expose only the object
        for key in data:
            data[key] = data[key][0]

        return data

    def pii_for_unique_md5s(self, unique_md5s: list[UniqueMD5]) -> list[MD5WithPII]:
        """
        Pull PII given a list of unique MD5s.

        Note that the resulting list will likely be of shorter len than the input
        given the PII hit rate is often less than 1.00. 
        """
        md5s_list: list[str] = [md5.md5 for md5 in unique_md5s]
        pii_data: dict[str, dict[str, Any]] = self._pull_pii(md5s_list)

        return_md5s: list[MD5WithPII] = []
        md5: UniqueMD5

        for md5 in unique_md5s:
            if not md5.md5 in pii_data:
                continue

            return_md5s.append(
                MD5WithPII(
                    md5=md5.md5,
                    sentences=md5.sentences,
                    pii=PII.from_api_dict(pii_data[md5.md5])
                )
            )

        return return_md5s
