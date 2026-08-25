import requests
from typing import *
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("action_card_cache_integrated")

class ActionCardCache:
    def __init__(self):
        self._storage = {}
        self._session = requests.Session()
        self._base_url = "https://localhost:8000/api/internal/action-cards/"
        self._restaurant_id = 2

        self.get_action_cards_from_dbi()

    def get_action_cards_from_dbi(self):
        try:
            response = self._session.get(self._base_url + f"active?restaurant_id={self._restaurant_id}")
            response.raise_for_status()
            self._cache_cards(response.json())
            logger.info(f"Successfully sent to endpoint")
            return True
        except requests.exceptions.RequestException as e:
            logger.error(f"Failed to sent to endpoint: {e}")
            return False

    def get_action_card_from_dbi_lookup(self, card_identifier: tuple):
        if len(card_identifier) != 2:
            return None

        action_type, object_class = card_identifier
        try:
            response = self._session.get(self._base_url + f"lookup?restaurant_id={self._restaurant_id}"
                                                          f"&action_type={action_type}&object_class={object_class}")
            response.raise_for_status()
            self._cache_cards(response.json())
            logger.info(f"Successfully sent to endpoint")
            return True
        except requests.exceptions.RequestException as e:
            logger.error(f"Failed to sent to endpoint: {e}")
            return False

    def get_action_card_from_cache(self, card_identifier: tuple):
        if len(card_identifier) != 2:
            return None

        action_type, object_class = card_identifier
        key = f"{action_type}_{object_class}"

        if key in self._storage:
            return self._storage[key]
        else:
            return None

    def _cache_cards(self, cards: List[dict]):
        if len(cards) == 0:
            return

        for card in cards:
            action_type = card["action_type"]
            object_class = card["object_class"]

            key = f"{action_type}_{object_class}"
            print("Card: ", card)
            print("Key: ", key)
            if key not in self._storage:
                self._storage[key] = card


if __name__ == "__main__":
    action_card_cache = ActionCardCache()
    action_card_cache.get_action_cards_from_dbi()
    print("Result: ", action_card_cache.get_action_card_from_cache(("packaging_order", "assembly_and_packing")))