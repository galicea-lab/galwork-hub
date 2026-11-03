from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from api.dependencies import get_db

from ..forms.registry import FormNotFoundError, get_form, list_forms
from ..schemas.form_instance import FormDataResponse, FormSaveRequest
from ..services.form_data import load_form_sections, save_form_sections

router = APIRouter(prefix="/api", tags=["forms"])


@router.get("/forms")
def get_forms_catalog():
    return list_forms()


@router.get("/forms/{slug}")
def get_form_schema(slug: str):
    try:
        return get_form(slug)
    except FormNotFoundError:
        raise HTTPException(status_code=404, detail=f"Formularz '{slug}' nie znaleziony")


@router.get("/projects/{project_id}/forms/{slug}", response_model=FormDataResponse)
def get_project_form(project_id: int, slug: str, db: Session = Depends(get_db)):
    try:
        form_def = get_form(slug)
    except FormNotFoundError:
        raise HTTPException(status_code=404, detail=f"Formularz '{slug}' nie znaleziony")

    sections = load_form_sections(db, project_id, form_def)
    return FormDataResponse(slug=slug, status="draft", version=1, sections=sections)


@router.put("/projects/{project_id}/forms/{slug}", response_model=FormDataResponse)
def save_project_form(
    project_id: int, slug: str, payload: FormSaveRequest, db: Session = Depends(get_db)
):
    try:
        form_def = get_form(slug)
    except FormNotFoundError:
        raise HTTPException(status_code=404, detail=f"Formularz '{slug}' nie znaleziony")

    save_form_sections(db, project_id, form_def, payload.sections, payload.status)
    sections = load_form_sections(db, project_id, form_def)
    return FormDataResponse(slug=slug, status=payload.status, version=1, sections=sections)
