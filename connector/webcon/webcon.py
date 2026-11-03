# coding: utf-8
"""
https://developer.webcon.com/docs/registration-and-authentiaction/
"""
import json

from .config import clientId, clientSecret, baseUrl,loginUrl
from .api_client import ApiClient
from .configuration import Configuration
from .rest import RESTClientObject

import base64

import logging

request_timeout = 50 # dobrać do posiadanej konfiguracji

class Webcon():
    configuration = Configuration()
    apiClient = None
    token=''

    def login(self):
        """
           curl -X POST "https://<host>/api/oauth2/token" -H "accept: text/plain" -H "Content-Type: application/json " \
                -d "{\"clientId\":\"xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx\",\"clientSecret\":\"xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx=\"}"
        """

        rest_client = RESTClientObject(self.configuration)
        response=rest_client.POST(loginUrl,
                                     query_params=None,
                                     headers={'accept': 'application/json',
                                               'Content-Type': 'application/x-www-form-urlencoded'},
                                     post_params={"grant_type":"client_credentials",
                                           "client_id":clientId,
                                           "client_secret":clientSecret},
                                     _preload_content=False,
                                     _request_timeout=request_timeout,
                                     body=None)
        try:
            data = json.loads(response.data)
            self.token=data['access_token']
            """
            'expires_in': 3600, 
            'scope': 'Admin.BusinessEntities.Read Admin.Connections.Read Admin.Connections.ReadWrite Admin.Constants.Read Admin.Constants.ReadWrite...nts.ReadWrite.f161089a-398a-4cc1-baa1-538bad59b1e7 HighTrust.Elements.ReadWrite.f69536bc-3889-4995-939a-4fdea9a14701 User.Data', 
            'token_type': 'Bearer'
            """
        except ValueError:
            self.token=''

        self.configuration.api_key['Authorization'] = self.token
        self.configuration.api_key_prefix['Authorization'] = 'Bearer'
        self.configuration.host=baseUrl
        self.apiClient=ApiClient(configuration=self.configuration)


    def get_applications(self):
        path='/applications'
        collection_formats = {}
        path_params = {}
        query_params = []
        header_params = {}
        form_params = []
        local_var_files = {}
        body_params = None
        # HTTP header `Accept`
        header_params['Accept'] = 'application/json'
        # Authentication setting
        auth_settings = ['Bearer']
        try:
          response = self.apiClient.call_api(
            path, 'GET',
            path_params,
            query_params,
            header_params,
            body=body_params,
            post_params=form_params,
            files=local_var_files,
            response_type=dict, #'V30ApplicationInfosCollection',  # noqa: E501
            auth_settings=auth_settings,
            async_req=False,
            _return_http_data_only=True,
            _preload_content= False,
            _request_timeout=request_timeout,
            collection_formats=collection_formats)
          return json.loads(response.data)['applications']
        except Exception as e:
            print(e)
            return[]


    def get_processes(self, idAplikacji):
        path='/applications/{}/processes'.format(idAplikacji)
        print(path)
        collection_formats = {}; path_params = {}; header_params = {}; local_var_files = {}
        query_params = []; form_params = []
        body_params = None
        header_params['Accept'] = 'application/json'
        auth_settings = ['Bearer']

        try:
          response = self.apiClient.call_api(
            path, 'GET',
            path_params,
            query_params,
            header_params,
            body=body_params,
            post_params=form_params,
            files=local_var_files,
            response_type=dict,
            auth_settings=auth_settings,
            async_req=False,
            _return_http_data_only=True,
            _preload_content= False,
            _request_timeout=request_timeout,
            collection_formats=collection_formats)
          return json.loads(response.data)['processes']
        except Exception as e:
            print(e)
            return[]

    def get_formTypes(self, idProcess):
        path = '/processes/{}/formtypes'.format(idProcess)
        collection_formats = {};
        path_params = {};
        header_params = {};
        local_var_files = {}
        query_params = [];
        form_params = []
        body_params = None
        header_params['Accept'] = 'application/json'
        auth_settings = ['Bearer']

        try:
            response = self.apiClient.call_api(
                path, 'GET',
                path_params,
                query_params,
                header_params,
                body=body_params,
                post_params=form_params,
                files=local_var_files,
                response_type=dict,
                auth_settings=auth_settings,
                async_req=False,
                _return_http_data_only=True,
                _preload_content=False,
                _request_timeout=request_timeout,
                collection_formats=collection_formats)
            return json.loads(response.data)['formTypes']
        except Exception as e:
            print(e)
            return []

    def get_workflows(self, idProcess):
        path='/processes/{}/workflows'.format(idProcess)
        collection_formats = {}; path_params = {}; header_params = {}; local_var_files = {}
        query_params = []; form_params = []
        body_params = None
        header_params['Accept'] = 'application/json'
        auth_settings = ['Bearer']

        try:
          response = self.apiClient.call_api(
            path, 'GET',
            path_params,
            query_params,
            header_params,
            body=body_params,
            post_params=form_params,
            files=local_var_files,
            response_type=dict,
            auth_settings=auth_settings,
            async_req=False,
            _return_http_data_only=True,
            _preload_content= False,
            _request_timeout=request_timeout,
            collection_formats=collection_formats)
          return json.loads(response.data)['workflows']
        except Exception as e:
            print(e)
            return[]

    def get_steps(self, idWorkflow):
        path='/workflows/{}/steps'.format(idWorkflow)
        collection_formats = {}; path_params = {}; header_params = {}; local_var_files = {}
        query_params = []; form_params = []
        body_params = None
        header_params['Accept'] = 'application/json'
        auth_settings = ['Bearer']

        try:
          response = self.apiClient.call_api(
            path, 'GET',
            path_params,
            query_params,
            header_params,
            body=body_params,
            post_params=form_params,
            files=local_var_files,
            response_type=dict,
            auth_settings=auth_settings,
            async_req=False,
            _return_http_data_only=True,
            _preload_content= False,
            _request_timeout=request_timeout,
            collection_formats=collection_formats)
          return json.loads(response.data)['steps']
        except Exception as e:
            print(e)
            return[]

    def get_formLayout(self, step, formType, dbId=1):
        path='/formlayout'
        query_params = {
            'step':step,
            'formType':formType,
            'dbId':dbId
        }
        path_params = {}; collection_formats = {}; header_params = {}; local_var_files = {}
        form_params = []
        body_params = None
        header_params['Accept'] = 'application/json'
        auth_settings = ['Bearer']

        try:
          response = self.apiClient.call_api(
            path, 'GET',
            path_params,
            query_params,
            header_params,
            body=body_params,
            post_params=form_params,
            files=local_var_files,
            response_type=dict,
            auth_settings=auth_settings,
            async_req=False,
            _return_http_data_only=True,
            _preload_content= False,
            _request_timeout=request_timeout,
            collection_formats=collection_formats)
          return json.loads(response.data)['fields']
        except Exception as e:
          print(e)
          return []

    def get_template(self, workflow,formtype,company):
        path='/elements/new'
        query_params = {
            'workflow' : workflow,
            'formtype' : formtype,
            'company' : company
        }
        path_params = {}; collection_formats = {}; header_params = {}; local_var_files = {}
        form_params = []
        body_params = None
        header_params['Accept'] = 'application/json'
        auth_settings = ['Bearer']

        try:
          response = self.apiClient.call_api(
            path, 'GET',
            path_params,
            query_params,
            header_params,
            body=body_params,
            post_params=form_params,
            files=local_var_files,
            response_type=dict,
            auth_settings=auth_settings,
            async_req=False,
            _return_http_data_only=True,
            _preload_content= False,
            _request_timeout=request_timeout,
            collection_formats=collection_formats)
          return json.loads(response.data)
        except Exception as e:
            print(e)
            print('?????????????')
            print(query_params)
            return  json.loads(e.body)

    def get_item(self, itemId):
        path = '/elements/{}'.format(itemId)
        query_params = { }
        path_params = {};
        collection_formats = {};
        header_params = {};
        local_var_files = {}
        form_params = []
        body_params = None
        header_params['Accept'] = 'application/json'
        auth_settings = ['Bearer']
        header_params["debug"]= 'true'

        try:
            response = self.apiClient.call_api(
                path, 'GET',
                path_params,
                query_params,
                header_params,
                body=body_params,
                post_params=form_params,
                files=local_var_files,
                response_type=dict,
                auth_settings=auth_settings,
                async_req=False,
                _return_http_data_only=True,
                _preload_content=False,
                _request_timeout=request_timeout,
                collection_formats=collection_formats)
            return json.loads(response.data)
        except Exception as e:
            print(e)
            return e.reason

    def get_item_list_values(self,id,field_id): #,workflow,formtype,company):
      #???/elements/resolveFieldValue/{idguid}
      #/elements/{id}/resolveFieldValue/{idguid}
      query_params = { }
      path = '/elements/{}/resolveFieldValue/{}'.format(id, field_id)
      #path = '/elements/resolveFieldValue/{}'.format(field_id)
      #query_params = { 'workflow': workflow,    'formtype': formtype,     'company': company }
      path_params = {};
      collection_formats = {};
      header_params = {};
      local_var_files = {}
      form_params = []
      body_params = { "value": "",  "formFields": [ ] }
      header_params['Accept'] = 'application/json'
      auth_settings = ['Bearer']
      header_params["debug"] = 'true'

      try:
        response = self.apiClient.call_api(
          path, 'POST',
          path_params,
          query_params,
          header_params,
          body=body_params,
          post_params=form_params,
          files=local_var_files,
          response_type=dict,
          auth_settings=auth_settings,
          async_req=False,
          _return_http_data_only=True,
          _preload_content=False,
          _request_timeout=request_timeout,
          collection_formats=collection_formats)

        return json.loads(response.data)['data']
      except Exception as e:
        print(e)
        raise e

    def get_attachement(self, itemId, attId):
        path = '/elements/{}/attachments/{}/content'.format(itemId,attId)
        query_params = { }
        path_params = {};
        collection_formats = {};
        header_params = {};
        local_var_files = {}
        form_params = []
        body_params = None
        header_params['Accept'] = 'application/json'
        auth_settings = ['Bearer']
        header_params["debug"]= 'true'

        try:
            response = self.apiClient.call_api(
                path, 'GET',
                path_params,
                query_params,
                header_params,
                body=body_params,
                post_params=form_params,
                files=local_var_files,
                response_type=dict,
                auth_settings=auth_settings,
                async_req=False,
                _return_http_data_only=True,
                _preload_content=False,
                _request_timeout=request_timeout,
                collection_formats=collection_formats)
            return  json.loads(response.data)['content']
        except Exception as e:
            print(e)
            return ''

     # raport  23,139 - !!! Sygnatura ID,Instance number,nr_albumu,Kierunek
    def get_report(self, idAplikacji, idRep, query_params = {}, idView = None):

      if idView:
        path = '/applications/{}/reports/{}/views/{}'.format(idAplikacji, idRep, idView)
      else:
        path = '/applications/{}/reports/{}'.format(idAplikacji, idRep)
      collection_formats = {};
      path_params = {};
      header_params = {};
      local_var_files = {}
#      query_params = [];
      form_params = []
      body_params = None
      header_params['Accept'] = 'application/json'
      auth_settings = ['Bearer']
      try:
        response = self.apiClient.call_api(
          path, 'GET',
          path_params,
          query_params,
          header_params,
          body=body_params,
          post_params=form_params,
          files=local_var_files,
          response_type=dict,
          auth_settings=auth_settings,
          async_req=False,
          _return_http_data_only=True,
          _preload_content=False,
          _request_timeout=request_timeout,
          collection_formats=collection_formats)
        return json.loads(response.data)
      except Exception as e:
        print(e)
        return {
          "count":0
        }



    def createInstance(self,data,epath='default'):
        path='/elements'
        path_params = {}; collection_formats = {}; local_var_files = {};header_params={}
        query_params = {
            'path' : epath,
        }
        form_params = []
        body_params = data
        header_params['Accept'] = 'application/json'
        header_params["debug"] = 'true'
        auth_settings = ['Bearer']

        try:
          response = self.apiClient.call_api(
            path, 'POST',
            path_params,
            query_params,
            header_params,
            body=body_params,
            post_params=form_params,
            files=local_var_files,
            response_type=dict,
            auth_settings=auth_settings,
            async_req=False,
            _return_http_data_only=True,
            _preload_content= False,
            _request_timeout=request_timeout,
            collection_formats=collection_formats)
          return response.data
        except Exception as e:
            print(e)
            raise Exception('Błąd danych: %s' % e)

    def updateInstance(self, id, data, epath=''):
      path = '/elements/%s' % id
      path_params = {};
      collection_formats = {};
      local_var_files = {};
      header_params = {}
      query_params = {
        'path': epath,
        #'forceCheckout': 1  # wymagane uprawnienia admina
      }
      form_params = []
      body_params = data
      header_params['Accept'] = 'application/json'
      header_params["debug"] = 'true'
      auth_settings = ['Bearer']

      try:
        response = self.apiClient.call_api(
          path, 'PATCH',
          path_params,
          query_params,
          header_params,
          body=body_params,
          post_params=form_params,
          files=local_var_files,
          response_type=dict,
          auth_settings=auth_settings,
          async_req=False,
          _return_http_data_only=True,
          _preload_content=False,
          _request_timeout=request_timeout,
          collection_formats=collection_formats)
        return response.data
      except Exception as e:
        print(e)
        raise Exception('Błąd danych: %s' % e)

    def prepare_new1(self, workflow, formtype, company, template, values):
          data = {
            "workflow": {"id": workflow},
            "formType": {"id": formtype},
            "company": {"id": company},
            "formFields": [],
            "comments": '', # {"newComment": values['comment']},
            "attachments": []}
          for field in template['formFields']:
            if field['name'] in values:
              v=values[field['name']]
              if type(v) == int:
                data["formFields"].append(
                  {"id": field['id'], "type": field['type'], "name": field['name'],
                   "value": values[field['name']]})
              elif field['type'] == 'ChoiceList' or field['type'] == 'ChoicePicker':
                if type(v)==str:
                  data["formFields"].append(
                    {"id": field['id'], "type": field['type'], "name": field['name'],
                    # "svalue": v,
                     "value":[{'id':v, 'name':v}]
                     })
                else:
                  data["formFields"].append(
                    {"id": field['id'], "type": field['type'], "name": field['name'],
                     "value": v
                     })
              else:
                data["formFields"].append(
                  {"id": field['id'], "type": field['type'], "name": field['name'],
                   "svalue": v})
            else:
              data["formFields"].append(
                {"id": field['id'], "type": field['type'], "name": field['name'],
                 "svalue": ''})
          return data

    def prepare_new(self, workflow,formtype,company,template,values,readonly=[]):
      data = {
          "workflow":{ "id":workflow},
          "formType":{"id":formtype},
          "company":{"id":company},
          "formFields":[],
#          "comments":{"newComment":values['comment']},
          "attachments":[]}
      if 'comment' in values:
        data['newComment']=values['comment']
      for field in template['formFields']:
        if not field['id'] in readonly:
          if field['name'] in values:
            if type(values[field['name']]) is dict:
              data["formFields"].append(
                {"id": field['id'], "type": field['type'], "name": field['name'],
                 "value": [values[field['name']]],
                 "svalue": '%s#%s' % (values[field['name']]['id'],values[field['name']]['name'])
                 })
            elif type(values[field['name']])==int:
              data["formFields"].append(
                  {"id": field['id'], "type": field['type'], "name": field['name'],
                  "value": values[field['name']]} )
              """
              elif field['type']=='ChoiceList' or field['type']=='ChoicePicker':
              if values[field['name']]['id']=='7a648f2d-6a3e-4da9-9271-21ffaeeb3bc6':
                data["formFields"].append(
                  {"id": field['id'], "type": field['type'], "name": field['name'],
                   "value": values[field['name']]['id'],
                   "svalue": values[field['name']]['name'],
                   })
              """
            else:
              data["formFields"].append(
                {"id": field['id'], "type": field['type'], "name": field['name'],
                "svalue": values[field['name']]} )
          else:
            if (field['svalue']):
              data["formFields"].append(
                  {"id": field['id'], "type": field['type'], "name": field['name'],
                   "value": field['value'],
                    "svalue": field['svalue']})
      return  data

    def prepare_new_update(self, workflow, formtype, company, template, values, readonly=[]):
        data = {
          "workflow": {"id": workflow},
          "formType": {"id": formtype},
          "company": {"id": company},
          "formFields": [],
          "attachments": []}
        if 'comment' in values:
          data['newComment'] = values['comment']
        for field in template['formFields']:
          if not field['id'] in readonly:
            if field['name'] in values:
              v=values[field['name']]
              if v:
                if type(v) is dict:
                  data["formFields"].append(
                    {"id": field['id'], "type": field['type'], "name": field['name'],
                     "value": v,
                     "svalue": '%s#%s' % (v['id'], v['name'])
                     })
                elif type(values[field['name']]) == int:
                  data["formFields"].append(
                    {"id": field['id'], "type": field['type'], "name": field['name'],
                     "value":v})
                else:
                  data["formFields"].append(
                    {"id": field['id'], "type": field['type'], "name": field['name'],
                     "svalue": v})
        return data

    def prepare_add_att(self, data, att_path='', att_name='', att_desc='', group=''):
      if att_path:
        with open(att_path, "rb") as afile:
          f64 = base64.b64encode(afile.read()).decode('utf-8')
          data["attachments"].append({
            "content": f64,
            "name": att_name,
            "description": att_desc,
            "group": group
          })
      return  data

    def prepare_att(self, att_path='', att_name='', att_desc='', group=''):
      if att_path:
        with open(att_path, "rb") as afile:
          f64 = base64.b64encode(afile.read()).decode('utf-8')
          return  {
            "content": f64,
            "name": att_name,
            "description": att_desc,
            "group": group
          }
      return {}

    def post_addAttach(self, element_id, NewAttachment, epath='default', force =False):
      log = logging.getLogger("Galwork-Webcon")
      path = '/elements/%s/attachments' % element_id
      path_params = {};
      collection_formats = {};
      local_var_files = {};
      header_params = {}
      if force:
        query_params = {
          'path': epath,
          'forceCheckout': 1
        }
      else:
        query_params = {
          'path': epath,
        }
      form_params = []
      body_params = NewAttachment
      header_params['Accept'] = 'application/json'
      header_params["debug"] = 'true'
      auth_settings = ['Bearer']

      try:
        response = self.apiClient.call_api(
          path, 'POST',
          path_params,
          query_params,
          header_params,
          body=body_params,
          post_params=form_params,
          files=local_var_files,
          response_type=dict,
          auth_settings=auth_settings,
          async_req=False,
          _return_http_data_only=True,
          _preload_content=False,
          _request_timeout=request_timeout,
          collection_formats=collection_formats)
        log.info('post_addAttach',response)
        return response.data
      except Exception as e:
        log.error('post_addAttach',e)
        #            print(e)
        raise e


    def get_Search(self, query,page=1,size=1):
      path = '/search'
      path_params = {}
      collection_formats = {}
      local_var_files = {}
      header_params = {}
      query_params = {'search':query,
                     'allWords':True,
                     'page':1,
                     'size':1}
      form_params = []
      body_params = None
      header_params['Accept'] = 'application/json'
      header_params["debug"] = 'true'
      auth_settings = ['Bearer']

      try:
        response = self.apiClient.call_api(
          path, 'GET',
          path_params,
          query_params,
          header_params,
          body=body_params,
          post_params=form_params,
          files=local_var_files,
          response_type=dict,
          auth_settings=auth_settings,
          async_req=False,
          _return_http_data_only=True,
          _preload_content=False,
          _request_timeout=request_timeout,
          collection_formats=collection_formats)
        return response.data
      except Exception as e:
        #            print(e)
        raise e

    def move(self, id, ppath):
      path = '/elements/%s' % id
      path_params = {}
      collection_formats = {}
      local_var_files = {}
      header_params = {}
      query_params = {'path':ppath,}
      form_params = []
      body_params = None
      header_params['Accept'] = 'application/json'
      header_params["debug"] = 'true'
      auth_settings = ['Bearer']
      try:
        response = self.apiClient.call_api(
          path, 'PATCH',
          path_params,
          query_params,
          header_params,
          body=body_params,
          post_params=form_params,
          files=local_var_files,
          response_type=dict,
          auth_settings=auth_settings,
          async_req=False,
          _return_http_data_only=True,
          _preload_content=False,
          _request_timeout=request_timeout,
          collection_formats=collection_formats)
        return response.data
      except Exception as e:
        #            print(e)
        raise e

    # alternatywa:
    # /api/data/v5.0/db/{dbId}/workflows/{idguid}/associatedFormTypes
    def formtypes(self, id):
      path = '/processes/%s/formtypes' % id
      path_params = {}
      collection_formats = {}
      local_var_files = {}
      header_params = {}
      query_params = {}
      form_params = []
      body_params = None
      header_params['Accept'] = 'application/json'
      header_params["debug"] = 'true'
      auth_settings = ['Bearer']
      try:
        response = self.apiClient.call_api(
          path, 'GET',
          path_params,
          query_params,
          header_params,
          body=body_params,
          post_params=form_params,
          files=local_var_files,
          response_type=dict,
          auth_settings=auth_settings,
          async_req=False,
          _return_http_data_only=True,
          _preload_content=False,
          _request_timeout=request_timeout,
          collection_formats=collection_formats)
        return response.data
      except Exception as e:
        #            print(e)
        raise e

    def formtypeMeta(self,guid):
      #[{'href': '/api/data/v3.0/db/1/formtypes/a0ae7e64-e98f-4e96-854a-77f8290d3cf4', 'method': 'GET', 'rel': 'self'}]
      # /api/data/v3.0/db/1/formtypes/223
      """
      {
        "description": null,
        "id": 223,
        "guid": "efd8994d-821f-4477-86ce-ef312cee7652",
        "name": "Podanie o ISS",
        "links": null
      }
      """
      path = '/formtypes/%s' % guid
      path_params = {}
      collection_formats = {}
      local_var_files = {}
      header_params = {}
      query_params = {}
      form_params = []
      body_params = None
      header_params['Accept'] = 'application/json'
      header_params["debug"] = 'true'
      auth_settings = ['Bearer']
      try:
        response = self.apiClient.call_api(
          path, 'GET',
          path_params,
          query_params,
          header_params,
          body=body_params,
          post_params=form_params,
          files=local_var_files,
          response_type=dict,
          auth_settings=auth_settings,
          async_req=False,
          _return_http_data_only=True,
          _preload_content=False,
          _request_timeout=request_timeout,
          collection_formats=collection_formats)
        return response.data
      except Exception as e:
        #            print(e)
        raise e


    def processesMeta(self, app):
      path = '/applications/%s/processes' % app
      path_params = {}
      collection_formats = {}
      local_var_files = {}
      header_params = {}
      query_params = {}
      form_params = []
      body_params = None
      header_params['Accept'] = 'application/json'
      header_params["debug"] = 'true'
      auth_settings = ['Bearer']
      try:
        response = self.apiClient.call_api(
          path, 'GET',
          path_params,
          query_params,
          header_params,
          body=body_params,
          post_params=form_params,
          files=local_var_files,
          response_type=dict,
          auth_settings=auth_settings,
          async_req=False,
          _return_http_data_only=True,
          _preload_content=False,
          _request_timeout=request_timeout,
          collection_formats=collection_formats)
        return response.data
      except Exception as e:
        #            print(e)
        raise e



    def processMeta(self, id):
      path = '/processes/%s' % id
      path_params = {}
      collection_formats = {}
      local_var_files = {}
      header_params = {}
      query_params = {}
      form_params = []
      body_params = None
      header_params['Accept'] = 'application/json'
      header_params["debug"] = 'true'
      auth_settings = ['Bearer']
      try:
        response = self.apiClient.call_api(
          path, 'GET',
          path_params,
          query_params,
          header_params,
          body=body_params,
          post_params=form_params,
          files=local_var_files,
          response_type=dict,
          auth_settings=auth_settings,
          async_req=False,
          _return_http_data_only=True,
          _preload_content=False,
          _request_timeout=request_timeout,
          collection_formats=collection_formats)
        return response.data
      except Exception as e:
        #            print(e)
        raise e

