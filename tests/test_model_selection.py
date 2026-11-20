import unittest
from unittest.mock import patch
from app.model_selection import control_model, choose_model
from tests.test_simple_server_stdlib import isolated_storage
import simple_server as server
class ModelSelectionTests(unittest.TestCase):
 def test_natural_model_names_and_short_answers(self):
  with isolated_storage():
   for text in ('Включи умную модель', 'включи модель 120', '120', '120Б', 'включи модель 120B'):
    self.assertIn('120b', control_model(server.storage, '1834373176', text))
    self.assertEqual(server.storage.model_selection(), '120b')
   for text in ('20', 'включи модель 20', 'переключись на лёгкую модель'):
    self.assertIn('20b', control_model(server.storage, '1834373176', text))
   self.assertIsNone(control_model(server.storage, '777', '120'))
   self.assertIsNone(control_model(server.storage, '1834373176', '120 рублей'))
 def test_owner_selection_persists(self):
  with isolated_storage():
   self.assertIsNone(control_model(server.storage, '777', 'смени модель на 120B'))
   self.assertEqual(server.storage.model_selection(), '20b')
   self.assertIn('120b', control_model(server.storage, '1834373176', 'смени модель на более крутую'))
   server.storage.init()
   self.assertEqual(server.storage.model_selection(), '120b')
   self.assertIn('20b', control_model(server.storage, '1834373176', 'переключись на лёгкую модель'))
 def test_auto(self):
  self.assertEqual(choose_model('auto', [{'role':'user','content':'привет'}]), '20b')
  self.assertEqual(choose_model('auto', [{'role':'user','content':'докажи теорему'}]), '120b')
