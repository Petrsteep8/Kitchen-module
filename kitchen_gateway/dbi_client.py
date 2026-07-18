import hashlib
import hmac
import json
import logging
import ast
import time
import requests
from typing import *
from kitchen_gateway.db_queue.db_queue import DBQueue
from kitchen_gateway.pydantic_models.pydantic_models import *

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("dbi_client_integrated")

class DBIClient:
    def __init__(self, base_url: str = 'https://localhost/api/ingest/', secret_key: bytes = None):
        self._base_url = base_url
        self._secret_key = secret_key
        self._session = requests.Session()
        self._db_queue = DBQueue(db_path='./kitchen_gateway/db_queue/db_queue.db')

    def _generate_signature(self, payload: Dict[str, Any]) -> str:
        message = json.dumps(payload, sort_keys=True)
        signature = hmac.new(self._secret_key, message.encode('utf-8'), hashlib.sha256).hexdigest()
        return signature

    def _send_request(self, event_type: str, endpoint: str, payload: Dict[str, Any]) -> bool:
        url = f"{endpoint}"
        signature = self._generate_signature(payload)
        timestamp = str(int(time.time()))
        if event_type == 'AUDIT_CLIP':
            headers = {'Content-Type': 'application/json', 'X-Signature': signature,
                       'X-Timestamp': timestamp, 'clip_id': payload['audit_clip_event_id']}
        else:
            headers = {'Content-Type': 'application/json', 'X-Signature': signature,
                       'X-Timestamp': timestamp, f'{payload['event_type'].lower()}_id':
                           payload[f'{payload['event_type'].lower()}_id']}

        try:
            response = self._session.post(url, json=payload, headers=headers, timeout=20, verify=True)
            response.raise_for_status()
            logger.info(f"Successfully sent to {endpoint}")
            return True
        except requests.exceptions.RequestException as e:
            logger.error(f"Failed to send to {endpoint}: {e}")
            return False

    def _route_url(self, event_type) -> str:
        url = ''
        if event_type == 'ACTION_EVENT':
            url = self._base_url + 'action-event'
        elif event_type == 'HYGIENE_CHECK':
            url = self._base_url + 'hygiene-check'
        elif event_type == 'SANITARY_EVENT':
            url = self._base_url + 'sanitary-event'
        elif event_type == 'PROCESS_EVENT':
            url = self._base_url + 'process-event'
        elif event_type == 'DATA_QUALITY_EVENT':
            url = self._base_url + 'data-quality-event'
        elif event_type == 'ENVIRONMENT_STATE':
            url = self._base_url + 'environment-state'
        elif event_type == 'AUDIT_CLIP':
            url = self._base_url + 'audit-clips'
        return url

    def validate_payload(self, event_type: str, payload: Dict[str, Any]) -> bool:
        print(event_type)
        if event_type == 'ACTION_EVENT':
            try:
                action = ActionEventLn(**payload)
                print(action.model_dump())
            except ValueError as e:
                print(e)
                return False
        elif event_type == 'HYGIENE_CHECK':
            try:
                hygiene = HygieneCheckLn(**payload)
                print(hygiene.model_dump())
            except ValueError as e:
                print(e)
                return False
        elif event_type == 'SANITARY_EVENT':
            try:
                sanitary = SanitaryEventLn(**payload)
                print(sanitary.model_dump())
            except ValueError as e:
                print(e)
                return False
        elif event_type == 'PROCESS_EVENT':
            try:
                process = ProcessEventLn(**payload)
                print(process.model_dump())
            except ValueError as e:
                print(e)
                return False
        elif event_type == 'DATA_QUALITY_EVENT':
            try:
                data_quality = DataQualityEventLn(**payload)
                print(data_quality.model_dump())
            except ValueError as e:
                print(e)
                return False
        elif event_type == 'ENVIRONMENT_STATE':
            try:
                environment = EnvironmentStateLn(**payload)
                print(environment.model_dump())
            except ValueError as e:
                print(e)
                return False
        elif event_type == 'AUDIT_CLIP':
            try:
                clip = AuditClipLn(**payload)
                print(clip.model_dump())
            except ValueError as e:
                print(e)
                return False
        return True

    def send_with_queue(self, payload: Dict[str, Any]):
        if len(payload) < 1:
            logger.error(f"Payload is empty. {len(payload)}")
            return

        event_type = payload['event_type']
        if not self.validate_payload(event_type=event_type, payload=payload):
            return

        url = self._route_url(event_type)
        if not self._send_request(event_type=event_type, endpoint=url, payload=payload):
            self._db_queue.add_database_event(pkt=payload)

    def process_queue(self):
        self._db_queue.check_size_and_cleanup()
        tasks = self._db_queue.get_database_events(limit=5)
        i = 0
        error_counter = 0
        retry_delay = 0.1

        while i < len(tasks):
            task = tasks[i]

            idf = task[0]
            payload = ast.literal_eval(task[1])
            event_type = payload['event_type']
            url = self._route_url(event_type)

            while True:
                if self._send_request(event_type=event_type, endpoint=url, payload=payload):
                    self._db_queue.update_completed(idf=idf)
                    i += 1
                    break
                else:
                    error_counter += 1
                    if error_counter >= 3:
                        logger.error(f"MAX attempts reached. {error_counter}")
                        self._db_queue.update_failed(idf=idf)
                        i += 1
                        error_counter = 0
                        break
                    time.sleep(retry_delay * (error_counter + 1))
        return None


# TESTS
if __name__ == '__main__':
    sanitary_data = {'event_type': 'SANITARY_EVENT', 'sanitary_event_id': str(uuid.uuid4()), 'sanitary_event': 'no_cap',
                     'severity': 'violation', 'severity_iternal': 'violation', 'escalation_category': '',}
    db = DBIClient()
    if db.validate_payload(event_type='SANITARY_EVENT', payload=sanitary_data):
        print(1)
    else:
        print(0)