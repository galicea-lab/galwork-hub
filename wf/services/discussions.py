# wf/services/discussions.py

"""
Dyskusje o sprawie w komunikatorze (domyślnie Mattermost).

Hub nie integruje się z komunikatorem (brak API, brak informacji zwrotnej):
  * "Rozpocznij dyskusję" otwiera adres z `[chat] start_url` (szablon,
    np. kanał zespołu); znaczniki {business_key}, {instance_id}, {title},
    {model_key} są zastępowane danymi sprawy (zakodowane do URL),
  * użytkownik wkleja do sprawy link do wątku (permalink) - hub go zapisuje
    i pokazuje uczestnikom sprawy.

Konfiguracja `[chat]`:
  * `provider` - nazwa komunikatora w interfejsie (domyślnie Mattermost),
  * `start_url` - szablon adresu; pusty = bez przycisku "Rozpocznij",
  * `allowed_hosts` - hosty dopuszczalnych linków (po przecinku); pusty =
    host z `start_url`, a bez niego dowolny host https.

Ustalenia z dyskusji przenosi się do formularza / opinii w obiegu -
dyskusja w komunikatorze nie jest dokumentacją sprawy.
"""
from __future__ import annotations

from urllib.parse import quote, urlparse

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from connector.base import BaseConnector
from wf.models import HumanTask, ProcessDiscussion, ProcessInstance
from wf.schemas.wf_schemas import CurrentUser, EventType

DEFAULT_PROVIDER = 'Mattermost'


class DiscussionError(Exception):
    def __init__(self, message: str, status: int = 422):
        super().__init__(message)
        self.status = status


def _config() -> dict:
    return BaseConnector.load_ini_section('chat') or {}


def provider() -> str:
    return (_config().get('provider') or '').strip() or DEFAULT_PROVIDER


def start_url(instance: ProcessInstance) -> str | None:
    template = (_config().get('start_url') or '').strip()
    if not template:
        return None
    values = {'business_key': instance.business_key or '', 'instance_id': str(instance.id),
              'title': instance.title or '', 'model_key': instance.process_model_key}
    for key, value in values.items():
        template = template.replace('{' + key + '}', quote(value, safe=''))
    return template


def _allowed_hosts() -> set[str]:
    config = _config()
    hosts = {h.strip().lower() for h in (config.get('allowed_hosts') or '').split(',') if h.strip()}
    if not hosts and (config.get('start_url') or '').strip():
        host = urlparse(config['start_url'].strip()).hostname
        if host:
            hosts.add(host.lower())
    return hosts


def validate_url(url: str) -> str:
    url = url.strip()
    parsed = urlparse(url)
    if parsed.scheme != 'https' or not parsed.hostname:
        raise DiscussionError('Link do dyskusji musi być adresem https')
    hosts = _allowed_hosts()
    if hosts and parsed.hostname.lower() not in hosts:
        raise DiscussionError(f'Link musi prowadzić do komunikatora ({", ".join(sorted(hosts))})')
    return url


def list_discussions(db: Session, instance: ProcessInstance) -> list[ProcessDiscussion]:
    return db.execute(select(ProcessDiscussion)
                      .where(ProcessDiscussion.process_instance_id == instance.id)
                      .options(selectinload(ProcessDiscussion.created_by))
                      .order_by(ProcessDiscussion.created_at)).scalars().all()


def add_discussion(db: Session, instance: ProcessInstance, user: CurrentUser, *, url: str,
                   title: str | None, task_id=None) -> ProcessDiscussion:
    from wf.services.process_service import get_service
    url = validate_url(url)
    if task_id is not None:
        task = db.get(HumanTask, task_id)
        if task is None or task.process_instance_id != instance.id:
            raise DiscussionError('Zadanie nie należy do tej sprawy', status=404)
    discussion = ProcessDiscussion(process_instance_id=instance.id, human_task_id=task_id, url=url,
                                   title=(title or '').strip() or None, created_by_id=user.id)
    db.add(discussion)
    db.flush()
    get_service().record_event(db, instance, EventType.DISCUSSION_LINKED, actor=user,
                               payload={'url': url, 'title': discussion.title,
                                        'task_id': str(task_id) if task_id else None})
    db.refresh(discussion)
    return discussion
