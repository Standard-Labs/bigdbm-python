"""Validate emails using MillionVerifier."""
import requests

from concurrent.futures import ThreadPoolExecutor

from bigdbm.schemas import MD5WithPII
from bigdbm.validate.base import BaseValidator


class EmailValidator(BaseValidator):
    """
    Remove emails determined to not be 'good' by MillionVerifier.
    """

    def __init__(self, million_key: str, max_threads: int = 10) -> None:
        """Initialize with MillionVerifier key."""
        self.api_key: str = million_key
        self.max_threads: int = max_threads

    def _validate_email(self, email: str) -> bool:
        """Validate an email with MillionVerifier."""
        response = requests.get(
            "https://api.millionverifier.com/api/v3",
            params={
                "api": self.api_key,
                "email": email,
                "timeout": 10
            }
        )

        response.raise_for_status()
        response_json = response.json()

        if "resultcode" not in response_json:
            raise ValueError(f"Unexpected response from MillionVerifier: {response_json}")

        return response_json["resultcode"] == 1

    def validate(self, md5s: list[MD5WithPII]) -> list[MD5WithPII]:
        """Remove any emails that are not 'good'."""
        # Extract all the emails
        all_emails: list[str] = []
        for md5 in md5s:
            all_emails.extend(md5.pii.emails)

        # Validate all the emails
        with ThreadPoolExecutor(max_workers=self.max_threads) as executor:
            valid_emails_idx: list[bool] = list(
                executor.map(self._validate_email, all_emails)
            )

        # Extract valid emails
        valid_emails: list[str] = [
            email for email, is_valid in zip(all_emails, valid_emails_idx) if is_valid
        ]

        # Remove invalid emails from MD5s
        for md5 in md5s:
            md5.pii.emails = [
                email for email in md5.pii.emails if email in valid_emails
            ]

        return md5s
