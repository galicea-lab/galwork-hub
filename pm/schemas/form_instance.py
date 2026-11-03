from typing import Any

from pydantic import BaseModel


class FormSaveRequest(BaseModel):
    """Payload zapisu formularza: {section_key: wartosc}.

    Wartosc to dict dla sekcji fields/narrative/signatures, lista dictow dla
    sekcji table/checklist. Sekcje ze struktura `data_source` inna niz
    "form_instance" sa dodatkowo materializowane do dedykowanych tabel
    (patrz `app/services/form_data.py`).
    """

    sections: dict[str, Any]
    status: str = "draft"


class FormDataResponse(BaseModel):
    slug: str
    status: str
    version: int
    sections: dict[str, Any]
