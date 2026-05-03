import hashlib
import hmac
import json
import logging
import ast
import time
from typing import Any, Dict
from kitchen_gateway.db_queue import DBQueue


import requests

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("dbi_client_integrated")


class DBIClient:
    def __init__(self, base_url: str, secret_key: bytes):
        self._base_url = base_url
        self._secret_key = secret_key
        self._session = requests.Session()
        self._db_queue = DBQueue()

    def _generate_signature(self, payload: Dict[str, Any]) -> str:
        message = json.dumps(payload, sort_keys=True)
        signature = hmac.new(
            self._secret_key,
            message.encode('utf-8'),
            hashlib.sha256
        ).hexdigest()
        return signature

    def _send_request(self, endpoint: str, payload: Dict[str, Any]) -> bool:
        url = f"{endpoint}"
        signature = self._generate_signature(payload)
        timestamp = str(int(time.time()))
        headers = {'Content-Type': 'application/json', 'X-Signature': signature, 'X-Timestamp': timestamp }

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
        if event_type == 'ACTION_START':
            url = self._base_url + 'action_events'
        elif event_type == 'ACTION_END':
            url = self._base_url + 'action_events'
        elif event_type == 'ACTION_INTERRUPTION':
            url = self._base_url + 'action_events'
        elif event_type == 'ZONE_RISK_SCORE':
            url = self._base_url + 'environment_state'
        elif event_type == 'SANITARY_EVENT':
            url = self._base_url + 'violations'
        return url

    def send_with_queue(self, payload: Dict[str, Any]):
        if len(payload) < 1:
            logger.error(f"Payload is empty. {len(payload)}")
            return

        event_type = payload['event_type']
        url = self._route_url(event_type)

        if not self._send_request(url, payload):
            self._db_queue.add_database_event(payload)

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
                if self._send_request(url, payload):
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