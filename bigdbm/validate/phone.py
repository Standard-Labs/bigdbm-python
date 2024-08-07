"""Validate phone numbers using numverify."""
import requests

from concurrent.futures import ThreadPoolExecutor

from bigdbm.schemas import MD5WithPII
from bigdbm.validate.base import BaseValidator


class PhoneValidator(BaseValidator):
    """
    Remove US phone numbers determined to not be 'valid' by Numverify.
    """

    def __init__(self, numverify_key: str, max_threads: int = 10) -> None:
        """Initialize with numverify key."""
        self.api_key: str = numverify_key
        self.max_threads: int = max_threads

    def _validate_phone(self, phone: str) -> bool:
        """Validate a US phone number with numverify."""
        phone = str(phone)
        if phone.startswith("+1"):
            phone = phone[2:]
        
        if len(phone) == 11 and phone.startswith("1"):
            phone = phone[1:]
        
        if len(phone) != 10:
            return False
        
        response = requests.get(
            "https://apilayer.net/api/validate",
            params={
                "access_key": self.api_key,
                "number": phone,
                "country_code": "US"
            }
        )

        response.raise_for_status()
        response_json = response.json()

        if "valid" not in response_json:
            raise ValueError(f"Unexpected response from numverify: {response_json}")

        return response_json["valid"]

    def validate(self, md5s: list[MD5WithPII]) -> list[MD5WithPII]:
        """Remove any phone numbers that are not 'good'."""
        # Extract all the phone numbers
        all_phones: list[str] = []
        for md5 in md5s:
            all_phones.extend([phone.phone for phone in md5.pii.mobile_phones])

        # Validate all the phone numbers
        with ThreadPoolExecutor(max_workers=self.max_threads) as executor:
            valid_phones_idx: list[bool] = list(
                executor.map(self._validate_phone, all_phones)
            )

        # Extract valid phone numbers
        valid_phones: list[str] = [
            phone for phone, is_valid in zip(all_phones, valid_phones_idx) if is_valid
        ]

        # Remove invalid phone numbers from MD5s
        for md5 in md5s:
            md5.pii.mobile_phones = [
                phone for phone in md5.pii.mobile_phones if phone.phone in valid_phones
            ]

        return md5s


class HasPhoneValidator(BaseValidator):
    """
    Only show hems with a phone number. 

    So, use this validator _after_ PhoneValidator so that phone numbers are not removed
    afterwards resulting in potentially empty phone lists.
    """

    def validate(self, md5s: list[MD5WithPII]) -> list[MD5WithPII]:
        """Remove hems without a phone number."""
        return [md5 for md5 in md5s if md5.pii.mobile_phones]


class DNCValidator(BaseValidator):
    """
    Only provide hems whose primary phone is not on the DNC list.
    Ignores hems without a phone number - these are still kept.
    """

    def validate(self, md5s: list[MD5WithPII]) -> list[MD5WithPII]:
        """
        Remove hems whose primary phone is marked as DNC.
        Ignores hems without a phone number - these are still kept.
        """
        return_hems: list[MD5WithPII] = []

        for md5 in md5s:
            if not md5.pii.mobile_phones:
                continue

            if not md5.pii.mobile_phones[0].do_not_call:
                return_hems.append(md5)

        return return_hems


class CallableValidator(BaseValidator):
    """
    Proxy for running both HasPhoneValidator and DNCValidator.
    Only show hems who are 'callable', meaning they have a phone number and are 
    not on the DNC list.
    """

    def __init__(
            self, 
            phone_validator: PhoneValidator | None = None, 
            dnc_validator: DNCValidator | None = None
        ):
        self.phone_validator = phone_validator or HasPhoneValidator()
        self.dnc_validator = dnc_validator or DNCValidator()

    def validate(self, md5s: list[MD5WithPII]) -> list[MD5WithPII]:
        """Remove hems without a phone number or on the DNC list."""
        return self.dnc_validator.validate(self.phone_validator.validate(md5s))
