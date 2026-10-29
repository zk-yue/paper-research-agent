from fastapi import APIRouter, HTTPException

from backend import preset_store, template_store
from backend.schemas import (
    CreatePresetRequest,
    CreateTemplateRequest,
    UpdatePresetRequest,
    UpdateTemplateRequest,
)

router = APIRouter(tags=["libraries"])


@router.get("/api/review-templates")
async def list_review_templates():
    return template_store.list_templates()


@router.get("/api/review-templates/{template_id}")
async def get_review_template(template_id: str):
    data = template_store.get_template(template_id)
    if not data:
        raise HTTPException(status_code=404, detail="Template not found")
    return data


@router.post("/api/review-templates")
async def create_review_template(req: CreateTemplateRequest):
    return template_store.create_template(req.journal_name, req.papers)


@router.put("/api/review-templates/{template_id}")
async def update_review_template(template_id: str, req: UpdateTemplateRequest):
    data = template_store.update_template(template_id, req.journal_name, req.papers)
    if not data:
        raise HTTPException(status_code=404, detail="Template not found")
    return data


@router.delete("/api/review-templates/{template_id}")
async def delete_review_template(template_id: str):
    if not template_store.delete_template(template_id):
        raise HTTPException(status_code=404, detail="Template not found")
    return {"deleted": True}


@router.get("/api/instruction-presets")
async def list_instruction_presets():

    return preset_store.list_presets()


@router.post("/api/instruction-presets")
async def create_instruction_preset(req: CreatePresetRequest):

    return preset_store.create_preset(req.label, req.content)


@router.put("/api/instruction-presets/{preset_id}")
async def update_instruction_preset(preset_id: str, req: UpdatePresetRequest):

    data = preset_store.update_preset(preset_id, req.label, req.content)
    if not data:
        raise HTTPException(status_code=404, detail="Preset not found")
    return data


@router.delete("/api/instruction-presets/{preset_id}")
async def delete_instruction_preset(preset_id: str):

    if not preset_store.delete_preset(preset_id):
        raise HTTPException(status_code=404, detail="Preset not found")
    return {"deleted": True}
