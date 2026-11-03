# coding: utf-8
# webcon - pomocne

from unidecode import unidecode
from connector.webcon import Webcon
from typing import Dict, List
from logging import Logger

def name2ident(name):
  return unidecode(str(name).lower().replace(" ", "_").replace("(", "_").replace(")", "_") \
      .replace("?", "_").replace(":", "_").replace('/','_'))


def get_form(company = 1,workflow = 102, formtype = 92):
  webcon_cl = Webcon()
  webcon_cl.login()
  template = webcon_cl.get_template(workflow=workflow, formtype=formtype, company=company)
  return template

def get_value_form_field(fields,name):
  for f in fields:
    if f['name']==name:
      if f['type']=='ChoiceList':
        return f['value'][0]['id']
      else:
        return f['value']
  return ''

def webcon_update_item1(webcon_cl,item,fieldName,fieldValue):
  header=item['header']
  data = webcon_cl.prepare_new_update(header['workflow']['id'], header['formType']['id'], \
                               header['businessEntity']['id'], item, {fieldName:fieldValue}, \
                                      readonly=[ ])
  instance=webcon_cl.updateInstance(int(header['id']), data, epath='')
  return instance


def report_to_list(webcon_cl: Webcon, app: int, rep: int, columns: str, \
                   logger: Logger, \
                   ) -> List:
  try:
    ret = webcon_cl.get_report(app, rep)
    logger.info('webcon/report/tab ret=%s' % ret)
    lret = []
    if ret['count'] > 0:
      cols = columns.split(',')
      nc = []
      for c in cols:
        n = -1
        i = 0
        while (n < 0) and (i < len(ret['columns'])) and (ret['columns'][i]['name'] != c):
          i = i + 1
        if (i < len(ret['columns'])):
          nc.append(i)
      for r in ret['rows']:
        row = []
        for n in nc:
          row.append(r['cells'][n]['value'])
        lret.append(row)
    return lret
  except Exception as e:
    logger.info('webcon/report/tab: error %s' % e)
    raise e


def create_instance(webcon_cl: Webcon, fields: Dict, \
                    workflow: int = 35, formtype: int = 32, company: int = 1):
  template = webcon_cl.get_template(workflow=workflow, formtype=formtype, company=company)
  data = webcon_cl.prepare_new(workflow, formtype, company, template, fields)
  instance = webcon_cl.createInstance(data)
  return instance
