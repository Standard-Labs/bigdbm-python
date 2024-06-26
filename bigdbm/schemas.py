"""Datatypes for working with the API."""
from pydantic import BaseModel, Field, field_validator, model_validator

from typing import Any, Self
from enum import Enum

from bigdbm.taxonomy import code_to_category


class ConfigDates(BaseModel):
    """Start and end dates as returned by the /config route."""
    start_date: str
    end_date: str


class IABJob(BaseModel):
    """Payload for creating an IAB job."""
    intent_categories: list[str] = Field(
        default_factory=list, description="List of IAB intent categories"
    )
    zips: list[str] = Field(
        default_factory=list, description="List of zip codes"
    )
    keywords: list[str] = Field(
        default_factory=list, description="List of keywords"
    )
    domains: list[str] = Field(
        default_factory=list, description="List of domains"
    )
    n_hems: int

    @model_validator(mode="after")
    def validate_job(self) -> Self:
        """Make sure the job has enough information to run."""
        if all(
            [
                not self.intent_categories,
                not self.keywords,
                not self.domains
            ]
        ):
            raise ValueError(
                "Need at least one of intent categories, keywords, or domains.\n" + str(self.model_dump())
            )

        return self

    def as_payload(self) -> dict[str, str]:
        """Convert into dictionary payload."""
        return {
            "IABs": ",".join(self.intent_categories),
            "Zips": ",".join(self.zips),
            "Keywords": ",".join(self.keywords),
            "Domains": ",".join(self.domains),
            "NumberOfHems": self.n_hems
        }


class IntentEvent(BaseModel):
    """
    MD5 intent event as returned by API.
    Timestamp not supported yet.
    """
    md5: str
    sentence: str


class UniqueMD5(BaseModel):
    """
    Unique MD5 with all the associated sentences.

    `sentences` should be defined as whatever comes back from the API.
    Field validation automatically transforms IAB _codes_ into _strings_
    and removes duplicates.
    """
    md5: str
    sentences: list[str]

    @field_validator("sentences", mode="after")
    def transform_iab_codes(sentences: list[str]) -> list[str]:
        """Convert any valid IAB codes into strings."""
        sentences = list(set(sentences))

        for pos, sentence in enumerate(sentences):
            if sentence.isnumeric():
                sentences[pos] = code_to_category(sentence)

        return sentences
    

class MobilePhone(BaseModel):
    """An individual's phone number."""
    phone: str
    do_not_call: bool


class Gender(str, Enum):
    """Classifications of genders."""
    MALE = "Male"
    FEMALE = "Female"
    UNKNOWN = "Unknown"
    EMPTY = ""


class PII(BaseModel):
    """
    PII in output code 10008 as returned by BigDBM API directly. 
    No transformations, only typed data validation.

    Instantiate with `.from_api_dict` to ensure correct pre-processing of 
    mobile phone data.
    """
    id: str = Field(..., alias="Id", description="BigDBM person ID")
    address: str = Field(..., alias="Address")
    city: str = Field(..., alias="City")
    first_name: str = Field(..., alias="First_Name")
    last_name: str = Field(..., alias="Last_Name")
    last_name: str = Field(..., alias="Last_Name")
    # national_consumer_database  # what is this, and what type?
    state: str = Field(..., alias="State")
    zip_code: str = Field(..., alias="Zip")
    emails: list[str] = Field(..., alias="Email_Array")
    gender: Gender = Field(..., alias="Gender")
    age: str = Field(..., alias="Age")
    n_household_children: str = Field(
        ..., alias="Children_HH", description="String number of children in the household"
    )
    credit_range: str = Field(..., alias="Credit_Range")
    home_owner_status: str = Field(
        ..., alias="Home_Owner", description="Descriptor of home ownership status"
    )
    household_income: str = Field(
        ..., alias="Income_HH", description="Descriptor of household income range"
    )
    household_net_worth: str = Field(
        ..., alias="Net_Worth_HH", description="Descriptor of household net worth"
    )
    marital_status: str = Field(
        ..., alias="Marital_Status", description="Descriptor of marital status"
    )
    occupation: str = Field(
        ..., alias="Occupation_Detail", description="Descriptor of occupation"
    )
    n_household_veterans: str = Field(
        ..., alias="Veteran_HH", description="String number of veterans in the household"
    )
    mobile_phones: list[MobilePhone] = []

    @classmethod
    def from_api_dict(cls, api_dict: dict[str, Any]) -> Self:
        """Read in the data and parse the mobile phones."""
        mobile_phones: list[MobilePhone] = []
        for i in range(1, 3+1):
            if f"Mobile_Phone_{i}" in api_dict:
                phone: str = api_dict[f"Mobile_Phone_{i}"]

                if not phone:
                    continue

                dnc: bool = True if api_dict[f"Mobile_Phone_{i}_DNC"] == "1" else False
                mobile_phones.append(MobilePhone(phone=phone, do_not_call=dnc))

        if not api_dict["Email_Array"]:
            api_dict["Email_Array"] = []

        return cls(**api_dict, mobile_phones=mobile_phones)

    def as_lead_export(self) -> dict[str, Any]:
        """
        Dictionary output ready for insertion into a lead export.
        Emails and phone numbers are separated into unique attributes.
        """
        export_dict: dict[str, Any] = self.model_dump()

        # Remove unwanted fields
        if "id" in export_dict:
            del export_dict["id"]

        if "national_consumer_database" in export_dict:
            del export_dict["national_consumer_database"]

        # Separate Emails
        export_dict["email_1"] = None
        export_dict["email_2"] = None
        export_dict["email_3"] = None

        for pos, email in enumerate(self.emails[:3], start=1):
            export_dict[f"email_{pos}"] = email
    
        del export_dict["emails"]

        # Separate phone numbers
        export_dict["phone_1"] = None
        export_dict["phone_1_dnc"] = None
        export_dict["phone_2"] = None
        export_dict["phone_2_dnc"] = None
        export_dict["phone_3"] = None
        export_dict["phone_3_dnc"] = None

        phone: MobilePhone
        for pos, phone in enumerate(self.mobile_phones, start=1):
            export_dict[f"phone_{pos}"] = phone.phone
            export_dict[f"phone_{pos}_dnc"] = phone.do_not_call
        
        del export_dict["mobile_phones"]

        # Convert gender to string
        export_dict["gender"] = export_dict["gender"].value

        return export_dict


class MD5WithPII(UniqueMD5):
    """
    A unique MD5 with several sentences but also including an attribute with
    a dictionary of PII data returned directly by the API.

    Note: In the future, instead of housing a top-level `pii` attribute, 
    encode each PII attribute with the appropriate data type for full model
    valudation.
    """
    pii: PII
