from edu.routers.frekwencja_api import router as router_frekwencja
from edu.routers.ja_api import router as router_ja
from edu.routers.korespondencja_api import router as router_korespondencja
from edu.routers.legitymacja_api import router as router_legitymacja
from edu.routers.oceny_api import router as router_oceny
from edu.routers.oplaty_api import router as router_oplaty
from edu.routers.plan_api import router as router_plan
from edu.routers.slowniki_api import router as router_slowniki
from edu.routers.studia_api import router as router_studia
from edu.routers.student_api import router as router_studenci
from edu.routers.zajecia_api import router as router_zajecia

__all__ = [
  'router_frekwencja',
  'router_ja',
  'router_korespondencja',
  'router_legitymacja',
  'router_oceny',
  'router_oplaty',
  'router_plan',
  'router_slowniki',
  'router_studia',
  'router_studenci',
  'router_zajecia',
]
