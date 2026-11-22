# uprawnienia WF dla dashboardu - jedyne źródło prawdy
# dla widoczności przycisku „Dodaj akt” (backend nadal egzekwuje blokadę
# przy starcie obiegu `dokumenty`).
from fastapi import APIRouter, Depends

from wf.dependencies import can_create_act, get_current_wf_user
from wf.schemas.wf_schemas import CurrentUser, WfPermissions

router = APIRouter(prefix='/me', tags=['me'])


@router.get('/permissions', response_model=WfPermissions)
def get_my_permissions(current_user: CurrentUser = Depends(get_current_wf_user)):
    return WfPermissions(can_create_act=can_create_act(current_user))
