#!/usr/bin/env python
# coding: utf-8
"""
e-Doręczenia - połączenie z Webcon  - moduł rozwojowy, nie używany w produkcji
"""
import base64
import json
from typing import List
from connector.edoreczenia.ua_model import Message, Evidence

import uuid

from connector.base import BaseConnector, ConnectorError
_edr_config = BaseConnector.load_ini_section('edr')
import logging
from connector.webcon import Webcon

from connector.edoreczenia.edr_connector import EdrConnector, mime


log = logging.getLogger(__name__)

test_edr = str(_edr_config.get('test_db', 'false')).strip().lower() in ('1', 'true', 'yes', 'tak')
if test_edr:
    log.warning('connector.edoreczenia: tryb testowy (baza sqlite api.db)')
    from connector.webcon.db_test import db_get_edr, db_insert_edr, init_db
    init_db()
else:
    from connector.webcon.db import db_get_edr, db_insert_edr


def ss(s):
  if s:
    return str(s)
  else:
    return ''

def create_instance(webcon_cl, msg : Message, path=1271, workflow = 240,
                    formtype = 355, # dt
                    company = 1
    ):
  template = webcon_cl.get_template(workflow=workflow, formtype=formtype, company=company)
  meta=msg.messageMetadata #msg['messageMetadata']
  ctrl=msg.messageControlData #msg['messageControlData']
  labels=''
  for l in ctrl.labels: #['labels']:
    try:
      labels+=', '+l.label if labels else l.label
    except:
      pass
  data = webcon_cl.prepare_new(workflow, formtype, company, template, {
    'textBody':msg.textBody,
    'Adres Nadawcy' : meta.from_.eDeliveryAddress,
#    'Nadawca': ss(meta.from_.contributor.companyName),
    'from_firstName' : ss(meta.from_.contributor.firstName),
    'from_lastName' : ss(meta.from_.contributor.lastName),
    'from_CompanyName' : ss(meta.from_.contributor.companyName),
    'Adres Odbiorcy' : ss(meta.to[0].eDeliveryAddress),
    'to_firstName' : ss(meta.to[0].contributor.firstName),
    'to_lastName' : ss(meta.to[0].contributor.lastName),
    'to_CompanyName' : ss(meta.to[0].contributor),
    'messageId': meta.messageId,
    'shippingService' : ss(meta.shippingService),
    'subject' : ss(meta.subject),
    'threadId':ss(meta.threadId),
    'timestamp':ss(meta.timestamp),
    'MessageType':ss(ctrl.messageType),
    'labels':  labels,
    'opened':ctrl.opened
  },readonly=[])
  instance=webcon_cl.createInstance(data,epath=path)
  return json.loads(instance.decode())


def insert_evidences(webcon_cl,id, evidences : List[Evidence],company=1):
  try:
    data0 = webcon_cl.get_item(id)
    header = data0['header']
    data = webcon_cl.prepare_new_update(header['workflow']['id'], header['formType']['id'], \
                                        company, data0,
                                        {},
                                        readonly=[])
    if data0['itemLists']:
      data['itemLists']=data0['itemLists']
    changed=False
    for ev in evidences:
      if ev.evidenceId:
        cells=[]
        id_cell=-1
        for c in data['itemLists'][0]['columns']:
          if c['name']=='evidenceId':
            id_cell=len(cells)
            v=ev.evidenceId
          elif c['name']=='messageId':
            v=ev.messageId
          elif c['name'] == 'createDate':
            v=str(ev.createDate)
          elif c['name'] == 'eventDate':
            v=str(ev.eventDate)
          elif c['name'] == 'reasonDetails':
            v=ev.reasonDetails[0]
          elif c['name'] == 'externalData':
            v=ev.externalData
          elif c['name'] == 'downloaded':
            v=ev.downloaded
          elif c['name'] == 'type':
            v = str(ev.type.value)
          elif c['name'] == 'from_eDA':
            v=ev.from_.eDeliveryAddress
          elif c['name'] == 'to_eDA':
            v=ev.to.eDeliveryAddress
          else:
            v=''
          cells.append({'id':c['id'],'svalue':v})
        found=None
        for r in data['itemLists'][0]['rows']:
          if (r['cells'][id_cell]['svalue']==ev.evidenceId):
            found = r
        if found is None:
          changed = True
          data['itemLists'][0]['rows'].append({
             'cells': cells
          })
    if changed:
      webcon_cl.updateInstance(id, data, '')
      webcon_cl.move(id, 'default')
  except Exception:
    log.exception('insert_evidences: błąd zapisu potwierdzeń dla id=%s', id)

def  inbox_to_webcon(test=False, app=66, rep=224):
  edrc=EdrConnector(test)
  lista_edr=edrc.inbox()
  try:
    webcon_cl = Webcon()
    webcon_cl.login()
    result=webcon_cl.get_report(app, rep, query_params = {'page':1,'size':2000})
    lista_webc = []
    if result['count']>0:
      n = 0
      for r in result['columns']:
        if r['name']=='messageId':
          id=n
        n=n+1
      for r in result['rows']:
        v=r['cells']
        lista_webc.append(r['cells'][id]['value'])
    for msgid in lista_edr:
      if not msgid in lista_webc:
        msg=edrc.message(msgid)
        instance=create_instance(webcon_cl, msg)
        id=instance['id']
        #att = msg.attachments['attachments']
        for a in msg.attachments:
          data = {
              "content": a.file.file.decode(), #a['file']['file'],
              "name": a.file.fileMetadata.filename, #a['file']['fileMetadata']['filename'],
              "description": a.file.fileMetadata.contentType, #a['file']['fileMetadata']['contentType'],
              "group": '1#Otrzymane'
            }
          res = webcon_cl.post_addAttach(id, data, epath='default')
          #print(res)
        insert_evidences(webcon_cl, id, msg.evidences)
  except Exception as e:
    print('Błąd: %s' % e)

def  fetch_evidences(test=False, app=66, rep=657):
  try:
    webcon_cl = Webcon()
    webcon_cl.login()
    result=webcon_cl.get_report(app, rep)
    msgids = []
    ids=[]
    if result['count']>0:
      n = 0
      msgid=-1
      idid=-1
      for r in result['columns']:
        if r['name']=='messageId':
          msgid = n
        if r['name'] == 'ID':
          idid = n
        n=n+1
      if msgid>=0:
        for r in result['rows']:
          v=r['cells']
          msgids.append(r['cells'][msgid]['value'])
          ids.append(r['cells'][idid]['value'])
        edrc=EdrConnector(test)
        i=0
        for msgid in msgids:
            msg = edrc.message(msgid)
            insert_evidences(webcon_cl, ids[i], msg.evidences)
#            fetch_evidences_att(ids[i], msgid)
            i=i+1
  except Exception as e:
    print('Błąd: %s' % e)


def update_messageId(webcon_cl, id, data, messageId):
  company=1
  header = data['header']
  udata = webcon_cl.prepare_new_update(header['workflow']['id'], header['formType']['id'], \
                                      company, data, \
                                      { 'messageId': messageId, },readonly=[])
  instance = webcon_cl.updateInstance(id, udata, epath='')
  return json.loads(instance.decode())

def update_instance(webcon_cl, id, data, msg):
  company=1
  meta=msg['messageMetadata']
  ctrl=msg['messageControlData']
  labels=''
  for l in ctrl['labels']:
    try:
      labels+=', '+l['label'] if labels else l['label']
    except:
      pass
  header = data['header']
  u={
    'Adres Nadawcy' : meta['from']['eDeliveryAddress'],
    'Adres Odbiorcy' : meta['to'][0]['eDeliveryAddress'],
    'messageId': meta['messageId'],
    'shippingService' : meta['shippingService'],
    'threadId':meta['threadId'],
    'timestamp':meta['timestamp'],
    'MessageType':ctrl['MessageType'],
    'labels': labels,
    'opened':ctrl['opened']
   }
  if 'contributor' in meta['from']:
    u['from_firstName'] = meta['from']['contributor']['firstName'] if 'firstName' in meta['from']['contributor'] else '',
    u['from_lastName'] = meta['from']['contributor']['lastName'] if 'lastName' in meta['from']['contributor'] else '',
    u['from_CompanyName'] = meta['from']['contributor']['CompanyName'] if 'CompanyName' in meta['from']['contributor'] else '',
  if 'contributor' in meta['to']:
    u['to_firstName'] = meta['to'][0]['contributor']['firstName'] if 'firstName' in meta['to'][0]['contributor'] else '',
    u['to_lastName'] = meta['to'][0]['contributor']['lastName'] if 'lastName' in meta['to'][0]['contributor'] else '',
    u['to_CompanyName'] = meta['to'][0]['contributor']['CompanyName'] if 'CompanyName' in meta['to'][0]['contributor'] else '',
  udata = webcon_cl.prepare_new_update(header['workflow']['id'], header['formType']['id'], \
                                      company, data, u, readonly=[])
  instance = webcon_cl.updateInstance(id, udata, epath='')
  webcon_cl.move(id,'default')
  return json.loads(instance.decode())




def send1_from_webcon(id):
  edrc=EdrConnector()
  try:
    webcon_cl = Webcon()
    webcon_cl.login()
    msg = webcon_cl.get_item(id)
    ade=''
    subject=''
    body=''
    for ff in msg['formFields']:
      if ff['name']=='Adres odbiorcy':
        ade = ff['svalue']
      elif ff['name'] == 'textBody':
        body = ff['svalue']
      elif ff['name'] == 'subject':
        subject = ff['svalue']
    if ade:
      if not subject:
        subject='Przesyłka'
      watts = msg['attachments']
      eatts=[]
      for a in watts:
        acontent=webcon_cl.get_attachement(id, a['id'])
        eatts.append( {
            'file64':acontent,
            'filename':a['name'],
            'contentType':mime(a['name']),
            'uuid':uuid.uuid4(),
            'description':a['description']
        } )
      smsg=edrc.send(ade,subject,body,eatts)
      messageId=smsg['MessageId']
      # za wczesnie
      """
      msg=edrc.message(msgid)
      insert_evidences(webcon_cl, id, msg['evidences']) 
      update_instance(webcon_cl,id,msg,smsg)
      """
      if messageId:
        update_messageId(webcon_cl, id, msg, messageId)
        webcon_cl.move(id,'default') # webcon nie przesuwa - konflikt
        return messageId
      else:
        return smsg['Status']
  except Exception as e:
    print('Błąd: %s' % e)
    return e.__str__()

def send_from_webcon(test=False):
  global test_edr
  app = 66
  rep = 307
  edrc=EdrConnector(test)
  try:
    webcon_cl = Webcon()
    webcon_cl.login()
    result=webcon_cl.get_report(app, rep)
    if result['count']>0:
      n=0
      iid=-1
      while (n<len(result['columns'])) and (iid<0):
        if result['columns'][n]['name']=='ID':
          iid=n
        else:
          n+=1
      for r in result['rows']:
        id=r['cells'][iid]['value']
        msg = webcon_cl.get_item(id)
        ade=''
        subject=''
        body=''
        for ff in msg['formFields']:
          if ff['name']=='Adres odbiorcy':
            ade = ff['svalue']
          elif ff['name'] == 'textBody':
            body = ff['svalue']
          elif ff['name'] == 'subject':
            subject = ff['svalue']
        jest=db_get_edr(int(id))
        if ade and (not jest):
          if not subject:
            subject='Przesyłka'
          watts = msg['attachments']
          eatts=[]
          for a in watts:
            acontent=webcon_cl.get_attachement(id, a['id'])
            eatts.append( {
                'file64':acontent,
                'filename':a['name'],
                'contentType':mime(a['name']),
                'uuid':uuid.uuid4(),
                'description':a['description']
            } )
          if test_edr==1:
            smsg={'Status':'ok'}
            messageId='test'
          else:
            smsg=edrc.send(ade,subject,body,eatts)
            messageId=smsg['MessageId']
          # za wczesnie
          """
          msg=edrc.message(msgid)
          insert_evidences(webcon_cl, id, msg['evidences']) 
          update_instance(webcon_cl,id,msg,smsg)
          """
          if messageId:
            db_insert_edr(int(id), messageId, smsg['Status'])
            update_messageId(webcon_cl, id, msg, messageId)
            webcon_cl.move(id,'default')
            return messageId
          else:
            logger = logging.getLogger("edr")
            logger.log('edoreczenia'+smsg['Status'])
            return smsg['Status']
  except Exception as e:
    print('Błąd: %s' % e)
    return e.__str__()

# evidences (ZIP, XML, PDF)
def fetch_evidences_att(id, messageId, log=None):
  try:
    webcon_cl = Webcon()
    webcon_cl.login()
    wmsg = webcon_cl.get_item(id)
    if wmsg:
      edrc = EdrConnector()
      zip=edrc.edr.PobranieDowodowZip(messageId)
      if zip:
        data = {
          "content": base64.b64encode(zip.data).decode('utf-8'),
          "name": 'evidences.zip',
          "description": 'Dowody techniczne (zip)',
          "group": '2#Poświadczenia'
        }
        res = webcon_cl.post_addAttach(id, data, epath='default', force=True)

      d = edrc.edr.PobranieDowodow(messageId)
      da = json.loads(d.data)
      if da:
        for e in da['Evidences']:
          if e['type'] == "BPOP" or e['type'] == 'BPWP':  # PDF doręczona or zaakceptowana
            epdf = edrc.edr.PobranieDowoduF(e['evidenceId'])
            data = {
              "content": base64.b64encode(epdf.data).decode('utf-8'),
              "name": e['type']+'.pdf',
              "description": 'Dowód '+('doręczenia' if e['type']=='BPOP' else 'odbioru'),
              "group": '2#Poświadczenia'
            }
            res = webcon_cl.post_addAttach(id, data, epath='default',force =True)
          if e['type'] == 'BPWX' or e['type'] == 'BPOX':  # XML: Wiadomość zaakceptowana or pomyślnie preawizowana adresatowi
            exml = edrc.edr.PobranieDowoduF(e['evidenceId'])
            #exml.data - binary
            #base64.b64encode(x).decode('utf-8') - zakodowany string
            data = {
              "content": base64.b64encode(exml.data).decode('utf-8'),
              "name": e['type']+'.xml',
              "description": 'Dowód XML '+('zaakceptowania' if e['type']=='BPWX' else 'preawizowania'),
              "group": '2#Poświadczenia'
            }
            res = webcon_cl.post_addAttach(id, data, epath='default',force =True)
  except Exception as e:
    if log:
      log.info('Exception webcon_update')
      log.info(e)
    else:
      print('Błąd: %s' % e)
  return None

#################################

def update_webcon(id, messageId, log=None):
  try:
    webcon_cl = Webcon()
    webcon_cl.login()
    wmsg = webcon_cl.get_item(id)
    if wmsg:
      if log:
        log.info('webcon_item=%s',id)
      edrc = EdrConnector()
      emsg=edrc.message(messageId)
      if log:
        log.info(emsg)
      if emsg:
        insert_evidences(webcon_cl, id, emsg['evidences'])
        return update_instance(webcon_cl,id,wmsg,emsg)
  except Exception as e:
    if log:
      log.info('Exception webcon_update')
      log.info(e)
    else:
      print('Błąd: %s' % e)
  return None

def test_ev():
  # test:
  edrc = EdrConnector()
  messageId = 'PPSA-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx'
  m = None
  if m:
    for e in m.evidences:
      if e.type.value == "BP.OP" or e.type.value == 'BP.WP':  # PDF doręczona or zaakceptowana
        ev = edrc.edr.PobranieDowodu(e.evidenceId)
      if e.type.value == 'BP.WX' or e.type.value == 'BP.OX':  # XML: Wiadomość zaakceptowana or pomyślnie preawizowana adresatowi
        evz = edrc.edr.PobranieDowodu(e.evidenceId)
  d = edrc.edr.PobranieDowodow(messageId)
  da = json.loads(d.data)
  # print(d.data)
  if da:
    for e in da['Evidences']:
      if e['type'] == "BPOP" or e['type'] == 'BPWP':  # PDF doręczona or zaakceptowana
        ev = edrc.edr.PobranieDowoduF(e['evidenceId'])
      if e['type'] == 'BPWX' or e[
        'type'] == 'BPOX':  # XML: Wiadomość zaakceptowana or pomyślnie preawizowana adresatowi
        evz = edrc.edr.PobranieDowoduF(e['evidenceId'])


if __name__ == "__main__":
#  test_ev()
  fetch_evidences_att(11111,'PPSA-E-xxxxxxxxxx')


