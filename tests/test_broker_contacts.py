import re
import unittest
from dataclasses import replace
from unittest.mock import Mock

from audit_app.bridge import ResoClient
from audit_app.config import load_config


def member(key, office='branch', role='Broker Manager', status='Active', email='broker@example.invalid'):
    return {'MemberKey': key, 'OfficeKey': office, 'MemberType': role, 'MemberStatus': status,
            'MemberEmail': email, 'MemberFullName': key, 'MemberFirstName': key}


class BrokerContactTests(unittest.TestCase):
    def client(self, offices=None, members=None, rosters=None):
        config = replace(load_config(), env='test', bridge_base_url='https://mls.example.invalid/odata', bridge_key='fake')
        client = ResoClient(config)
        records = {'Office': offices or {}, 'Member': members or {}}
        client._one = Mock(side_effect=lambda resource, key, cache: records[resource].get(key, {}))
        def roster(resource, params):
            key = re.search("OfficeKey eq '([^']+)'", params['$filter']).group(1)
            return iter((rosters or {}).get(key, []))
        client._collection = Mock(side_effect=roster)
        return client

    def test_explicit_broker_has_priority_and_avoids_roster_reads(self):
        broker = member('bor', role='Broker of Record')
        client = self.client(members={'bor': broker})
        self.assertEqual(client._resolve_broker({'OfficeBrokerKey':'bor'}, 'branch'), ('bor', broker))
        client._collection.assert_not_called()

    def test_roster_broker_of_record_precedes_manager(self):
        broker, manager = member('bor', role='Broker of Record'), member('manager')
        client = self.client(rosters={'branch':[manager, broker]})
        office = {'OfficeKey':'branch'}
        self.assertEqual(client._resolve_broker(office, 'branch'), ('bor', broker))

    def test_explicit_manager_must_have_a_qualified_role(self):
        manager = member('manager')
        client = self.client(members={'manager': manager})
        self.assertEqual(client._resolve_broker({'OfficeManagerKey':'manager'}, 'branch'), ('manager', manager))
        client = self.client(members={'manager': member('manager', role='Salesperson')})
        self.assertEqual(client._resolve_broker({'OfficeManagerKey':'manager'}, 'branch'), (None, {}))

    def test_local_manager_precedes_head_office_broker(self):
        manager = member('manager')
        client = self.client(rosters={'branch':[manager]}, offices={'head':{'OfficeBrokerKey':'bor'}},
                             members={'bor':member('bor', office='head', role='Broker of Record')})
        self.assertEqual(client._resolve_broker({'MainOfficeKey':'head'}, 'branch'), ('manager', manager))
        self.assertFalse(any(call.args[0] == 'Office' for call in client._one.call_args_list))

    def test_head_office_broker_pointer_can_reference_a_different_roster(self):
        broker = member('bor', office='another-branch', role='Broker of Record')
        client = self.client(offices={'head':{'OfficeBrokerKey':'bor'}}, members={'bor':broker})
        self.assertEqual(client._resolve_broker({'MainOfficeKey':'head'}, 'branch'), ('bor', broker))

    def test_head_office_roster_manager_is_used_when_broker_missing(self):
        manager = member('manager', office='head')
        client = self.client(offices={'head':{'OfficeKey':'head'}}, rosters={'head':[manager]})
        self.assertEqual(client._resolve_broker({'MainOfficeKey':'head'}, 'branch'), ('manager', manager))

    def test_inactive_invalid_and_ordinary_brokers_are_not_fallback_recipients(self):
        client = self.client(rosters={'branch':[member('inactive', status='Inactive'),
            member('invalid', email='bad'), member('ordinary', role='Broker'),
            member('other-office', office='other'), member('good')]},
            members={'inactive-bor':member('inactive-bor', status='Inactive', role='Broker of Record')})
        key, profile = client._resolve_broker({'OfficeBrokerKey':'inactive-bor'}, 'branch')
        self.assertEqual(key, 'good')
        self.assertEqual(profile['MemberStatus'], 'Active')

    def test_ambiguous_roster_does_not_choose_arbitrarily_or_escalate(self):
        client = self.client(rosters={'branch':[member('one'),member('two')]})
        self.assertEqual(client._resolve_broker({'MainOfficeKey':'head'}, 'branch'), (None, {}))
        self.assertFalse(any(call.args[0] == 'Office' for call in client._one.call_args_list))

    def test_parent_cycles_and_depth_are_bounded(self):
        client = self.client(offices={'head':{'MainOfficeKey':'branch'}})
        self.assertEqual(client._resolve_broker({'MainOfficeKey':'head'}, 'branch'), (None, {}))
        self.assertEqual(client._collection.call_count, 2)
        offices = {str(i):{'MainOfficeKey':str(i+1)} for i in range(1,10)}
        client = self.client(offices=offices)
        self.assertEqual(client._resolve_broker({'MainOfficeKey':'1'}, 'branch'), (None, {}))
        self.assertEqual(client._collection.call_count, 5)

    def test_roster_bound_and_per_client_cache(self):
        client = self.client(rosters={'branch':[member(str(i)) for i in range(101)]})
        self.assertEqual(client._resolve_broker({'OfficeKey':'branch'}, 'branch'), (None, {}))
        client._resolve_broker({'OfficeKey':'branch'}, 'branch')
        self.assertEqual(client._collection.call_count, 1)

    def test_old_custom_mapping_retains_direct_broker_without_fallback(self):
        client = self.client(members={'bor':member('bor')})
        mapping = {name:dict(fields) for name,fields in client.config.field_map.items()}
        for name in ('type','status','office_id'):
            mapping['Member'].pop(name)
        client.config = replace(client.config, field_map=mapping)
        self.assertEqual(client._resolve_broker({'OfficeBrokerKey':'bor'}, 'branch')[0], 'bor')
        client._collection.assert_not_called()
