import re
import unicodedata

from pydantic import BaseModel, EmailStr, Field, field_validator

ETATS_BRESILIENS = {
    "AC", "AL", "AP", "AM", "BA", "CE", "DF", "ES", "GO", "MA", "MT", "MS",
    "MG", "PA", "PB", "PR", "PE", "PI", "RJ", "RN", "RS", "RO", "RR", "SC",
    "SP", "SE", "TO",
}


def simplifier_ville(city: str) -> str:
    normalized = unicodedata.normalize("NFKD", city)
    ascii_city = "".join(char for char in normalized if not unicodedata.combining(char))
    return " ".join(ascii_city.strip().lower().split())


class Registration(BaseModel):
    email: EmailStr
    password: str = Field(min_length=12, max_length=128)
    full_name: str = Field(min_length=2, max_length=100)
    city: str = Field(min_length=2, max_length=100)
    state: str = Field(min_length=2, max_length=2)
    postal_code: str = Field(min_length=5, max_length=5)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: EmailStr) -> str:
        return str(value).strip().lower()

    @field_validator("full_name", "city")
    @classmethod
    def strip_text(cls, value: str) -> str:
        return value.strip()

    @field_validator("state")
    @classmethod
    def valid_state(cls, value: str) -> str:
        state = value.strip().upper()
        if state not in ETATS_BRESILIENS:
            raise ValueError("État brésilien inconnu")
        return state

    @field_validator("postal_code")
    @classmethod
    def valid_postal_code(cls, value: str) -> str:
        if not re.fullmatch(r"\d{5}", value):
            raise ValueError("Le code postal doit contenir 5 chiffres")
        return value
