from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from api.dependencies import get_db
from api.security import CurrentPrincipal, get_current_principal
from bcs.models.member import MemberUser, NetworkMember
from bcs.schemas.member import NetworkMemberOut

router = APIRouter(prefix="/api/me", tags=["me"])


@router.get("/memberships", response_model=list[NetworkMemberOut])
def my_memberships(
    principal: CurrentPrincipal = Depends(get_current_principal),
    db: Session = Depends(get_db),
):
    """Uczestnicy (we wszystkich sieciach), w imieniu których może działać
    zalogowany użytkownik - powiązania z POST .../members/{id}/users.
    Z natury wymaga tokenu: bez niego nie wiadomo, kim jest wołający."""
    return db.execute(
        select(NetworkMember)
        .join(MemberUser, MemberUser.member_id == NetworkMember.id)
        .where(MemberUser.user_sub == principal.token.sub)
        .order_by(NetworkMember.member_code)
    ).scalars().all()
