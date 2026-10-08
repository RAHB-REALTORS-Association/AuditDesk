"""RESO Web API/OData client; this module name is retained for compatibility."""
import json
import logging
import math
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from itertools import islice


from .board_scope import is_eligible_listing
from .eligibility import EligibilityRules, membership_code
from .emailer import valid_email


LOG = logging.getLogger(__name__)


def retry_after_seconds(value, now=None):
    """Accept either Retry-After format without retrying before the server permits."""
    if not value:
        return None
    try:
        delay = float(value)
        return max(0, delay) if math.isfinite(delay) else None
    except ValueError:
        try:
            deadline = parsedate_to_datetime(value)
            if deadline.tzinfo is None:
                deadline = deadline.replace(tzinfo=timezone.utc)
            return max(0, (deadline - (now or datetime.now(timezone.utc))).total_seconds())
        except (TypeError, ValueError, OverflowError):
            return None


class ResoError(Exception):
    pass


def office_address(office, fields):
    """Format the physical address supplied by an MLS Office profile."""
    street = [str(office.get(fields[key]) or "").strip() for key in ("address1", "address2")]
    locality = [str(office.get(fields[key]) or "").strip() for key in ("city", "province", "postal_code")]
    return ", ".join(part for part in (*street, " ".join(part for part in locality if part)) if part) or None


class ResoClient:
    def __init__(self, config, rules=None):
        if config.env == "development":
            raise ResoError("Live RESO Web API access is unavailable in development")
        self.config = config
        self.base = config.bridge_base_url.rstrip("/")
        if not self.base.startswith("https://"):
            raise ResoError("RESO_BASE_URL must use HTTPS and identify the MLS OData service root")
        if not config.bridge_key:
            raise ResoError("RESO_API_KEY is required (legacy BRIDGE_API_KEY is also accepted)")
        self.rules = rules or EligibilityRules()
        self.properties = {}
        self.members = {}
        self.offices = {}
        self.office_rosters = {}
        self.broker_contacts = {}

    def _get(self, url):
        if self.config.env == "development":
            raise ResoError("Live RESO Web API access is unavailable in development")
        if not url.startswith(self.base + "/"):
            raise ResoError("RESO Web API pagination URL left the configured MLS service root")
        headers = {"Accept": "application/json"}
        if self.config.bridge_auth_mode == "bearer":
            headers["Authorization"] = "Bearer " + self.config.bridge_key
        else:
            parts = urllib.parse.urlsplit(url)
            query = urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
            query.append(("access_token", self.config.bridge_key))
            url = urllib.parse.urlunsplit((parts.scheme, parts.netloc, parts.path, urllib.parse.urlencode(query), parts.fragment))
        for attempt in range(6):
            delay = 2 ** attempt
            try:
                with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=30) as response:
                    return response.read()
            except urllib.error.HTTPError as exc:
                exc.close()
                if exc.code not in (429, 500, 502, 503, 504) or attempt == 5:
                    raise ResoError(f"MLS Web API returned HTTP {exc.code}") from None
                requested = retry_after_seconds(exc.headers.get("Retry-After") if exc.headers else None)
                delay = requested if requested is not None else (min(60 * 2 ** attempt, 900) if exc.code == 429 else delay)
                if delay > 900:
                    raise ResoError("MLS Web API requires a retry delay above 15 minutes; retry intake later") from None
                LOG.warning("reso_retry status=%s delay_seconds=%s", exc.code, delay)
            except (urllib.error.URLError, TimeoutError):
                if attempt == 5:
                    raise ResoError("MLS Web API request failed after retries") from None
            time.sleep(delay)

    def inspect_metadata(self):
        if not self.config.field_map.get('Member', {}).get('membership_class'):
            raise ResoError('Member field map must configure membership_class before live intake.')
        root = ET.fromstring(self._get(self.base + "/$metadata"))
        namespace = {"e": "http://docs.oasis-open.org/odata/ns/edm"}
        result = {}
        for entity in root.findall(".//e:EntityType", namespace):
            name = entity.attrib.get("Name")
            if name in ("Property", "Member", "Office"):
                result[name] = {p.attrib["Name"]: p.attrib.get("Type", "") for p in entity.findall("e:Property", namespace)}
        for resource, mapping in self.config.field_map.items():
            if resource not in result:
                raise ResoError(f"RESO Web API metadata has no {resource} resource")
            missing = [field for field in mapping.values() if field not in result[resource]]
            if missing:
                raise ResoError(f"{resource} mapping has fields absent from RESO Web API metadata: {', '.join(missing)}")
        membership_field = self.config.field_map['Member']['membership_class']
        if result['Member'][membership_field] != 'Edm.String':
            raise ResoError('Member membership_class must map to an Edm.String field.')
        entry_field = self.config.field_map["Property"]["entry_timestamp"]
        if result["Property"][entry_field] != "Edm.DateTimeOffset":
            raise ResoError(f"{entry_field} is not an Edm.DateTimeOffset field")
        return result

    def _collection(self, resource, params):
        url = self.base + "/" + resource + "?" + urllib.parse.urlencode(params)
        while url:
            try:
                data = json.loads(self._get(url))
            except (ValueError, KeyError):
                raise ResoError("MLS Web API returned invalid JSON") from None
            for row in data.get("value", []):
                yield row
            next_url = data.get("@odata.nextLink")
            url = urllib.parse.urljoin(self.base + "/", next_url) if next_url else None

    def _one(self, resource, key, cache):
        if not key:
            return {}
        if key not in cache:
            mapping = self.config.field_map[resource]
            safe_key = str(key).replace("'", "''")
            params = {"$filter": f"{mapping['key']} eq '{safe_key}'", "$select": ",".join(dict.fromkeys(mapping.values())), "$top": 1}
            cache[key] = next(self._collection(resource, params), {})
        return cache[key]

    def _usable_contact(self, member, roles=None):
        fields = self.config.field_map['Member']
        if not valid_email(member.get(fields['email'])):
            return False
        if fields.get('status') and str(member.get(fields['status']) or '').strip().casefold() != 'active':
            return False
        return roles is None or str(member.get(fields['type']) or '').strip().casefold() in roles

    def _office_contact(self, office, office_id):
        """Return contact key/profile and whether ambiguity prevents fallback."""
        offices, members = self.config.field_map['Office'], self.config.field_map['Member']
        broker_id = office.get(offices['broker_id'])
        broker = self._one('Member', broker_id, self.members)
        if self._usable_contact(broker):
            return broker_id, broker, False
        if not all(members.get(field) for field in ('type', 'status', 'office_id')):
            return None, {}, False
        if office_id not in self.office_rosters:
            key = str(office_id).replace("'", "''")
            params = {'$filter': f"{members['office_id']} eq '{key}' and {members['status']} eq 'Active' and ({members['type']} eq 'Broker of Record' or {members['type']} eq 'Broker Manager')",
                      '$select': ','.join(dict.fromkeys(members.values())), '$top': 100}
            self.office_rosters[office_id] = list(islice(self._collection('Member', params), 101))
        roster = self.office_rosters[office_id]
        if len(roster) > 100:
            return None, {}, True
        for role in ('broker of record', 'broker manager'):
            if role == 'broker manager' and offices.get('manager_id'):
                manager_id = office.get(offices['manager_id'])
                manager = self._one('Member', manager_id, self.members)
                if self._usable_contact(manager, {'broker manager', 'broker of record'}):
                    return manager_id, manager, False
            candidates = {row[members['key']]: row for row in roster
                          if row.get(members['key']) and row.get(members['office_id']) == office_id
                          and self._usable_contact(row, {role})}
            if len(candidates) == 1:
                return *next(iter(candidates.items())), False
            if len(candidates) > 1:
                return None, {}, True
        return None, {}, False

    def _resolve_broker(self, office, office_id):
        if office_id in self.broker_contacts:
            return self.broker_contacts[office_id]
        visited = set()
        current, key = office, office_id
        result = (None, {})
        for depth in range(5):
            if not key or key in visited or not current:
                break
            visited.add(key)
            contact_id, member, ambiguous = self._office_contact(current, key)
            if contact_id or ambiguous:
                result = (contact_id, member)
                break
            parent_field = self.config.field_map['Office'].get('parent_id')
            key = current.get(parent_field) if parent_field else None
            if not key or key in visited or depth == 4:
                break
            current = self._one('Office', key, self.offices)
        self.broker_contacts[office_id] = result
        return result

    def active_new_listings(self, start, end):
        self.inspect_metadata()
        fields = self.config.field_map["Property"]
        start_text = start.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
        end_text = end.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
        excluded_ids = tuple(dict.fromkeys(('NONMEM', *self.rules.agent_ids)))
        exclusions = ' and '.join(fields['agent_mls_id'] + " ne '" + value.replace("'", "''") + "'" for value in excluded_ids)
        params = {
            "$filter": f"{fields['originating_system_name']} eq 'Cornerstone' and {exclusions} and {fields['status']} eq 'Active' and {fields['entry_timestamp']} ge {start_text} and {fields['entry_timestamp']} lt {end_text}",
            "$select": ",".join(dict.fromkeys(fields.values())),
            "$top": 200,
            "$orderby": fields["entry_timestamp"] + " asc",
        }
        for row in self._collection("Property", params):
            listing = {name: row.get(field) for name, field in fields.items()}
            board = listing.get('originating_system_name')
            if not isinstance(board, str) or board.strip() != 'Cornerstone':
                continue
            listing['originating_system_name'] = board.strip()
            if not is_eligible_listing(listing, self.rules):
                continue
            listing['agent_mls_id'] = listing['agent_mls_id'].strip()
            if not listing["listing_id"] or not listing["entry_timestamp"]:
                raise ResoError("An MLS listing is missing its ID or entry timestamp")
            entered = datetime.fromisoformat(str(listing["entry_timestamp"]).replace("Z", "+00:00"))
            if entered.tzinfo is None:
                raise ResoError("MLS entry timestamp has no timezone")
            if listing["status"] != "Active" or not start <= entered < end:
                continue
            enriched = self._enrich_listing(listing)
            if is_eligible_listing(enriched, self.rules):
                yield enriched

    def listing_by_key(self, key):
        """Read a specific listing independently of its age or current status."""
        fields = self.config.field_map['Property']
        safe_key = str(key).replace("'", "''")
        rows = list(self._collection('Property', {
            '$filter': f"{fields['listing_id']} eq '{safe_key}'",
            '$select': ','.join(dict.fromkeys(fields.values())), '$top': 2}))
        if len(rows) != 1 or str(rows[0].get(fields['listing_id'])) != str(key):
            raise ResoError('MLS Web API could not uniquely identify the requested listing.')
        listing = {name: rows[0].get(field) for name, field in fields.items()}
        for name in ('originating_system_name','agent_mls_id'):
            if isinstance(listing.get(name), str):
                listing[name] = listing[name].strip()
        if not listing['entry_timestamp']:
            raise ResoError('MLS Web API returned a listing without an entry timestamp.')
        try:
            entered = datetime.fromisoformat(str(listing['entry_timestamp']).replace('Z', '+00:00'))
        except ValueError:
            raise ResoError('MLS Web API returned an invalid entry timestamp.') from None
        if entered.tzinfo is None or not listing.get('status'):
            raise ResoError('MLS Web API returned a listing without a timezone or status.')
        return self._enrich_listing(listing)

    def _enrich_listing(self, listing):
        member_map = self.config.field_map['Member']
        agent = {}
        if is_eligible_listing(listing, self.rules):
            agent = self._one('Member', listing.get('agent_id'), self.members)
        listing['agent_membership_class'] = membership_code(agent.get(member_map['membership_class'])) or None
        if not is_eligible_listing(listing, self.rules, require_class=True):
            listing['brokerage_id'] = listing.pop('office_id', None)
            listing['brokerage_name'] = listing.pop('office_name', None)
            for name in ('brokerage_email','brokerage_address','broker_id','broker_name','broker_first_name','broker_email'):
                listing[name] = None
            listing['mls_number'] = str(listing['mls_number'] or listing['listing_id'])
            listing['address'] = listing['address'] or 'Address unavailable'
            return listing
        office = self._one("Office", listing.get("office_id"), self.offices)
        office_map = self.config.field_map["Office"]
        member_map = self.config.field_map["Member"]
        broker_id, broker = self._resolve_broker(office, listing.get('office_id'))
        listing["agent_name"] = listing.get("agent_name") or agent.get(member_map["name"])
        listing["agent_email"] = listing.get("agent_email") or agent.get(member_map["email"])
        listing["brokerage_name"] = listing.get("office_name") or office.get(office_map["name"])
        listing["brokerage_id"] = listing.pop("office_id", None)
        listing.pop("office_name", None)
        listing["brokerage_email"] = office.get(office_map["email"])
        listing["brokerage_address"] = office_address(office, office_map)
        listing["broker_id"] = broker_id
        listing["broker_name"] = broker.get(member_map["name"])
        listing["broker_first_name"] = broker.get(member_map["first_name"])
        listing["broker_email"] = broker.get(member_map["email"])
        listing["mls_number"] = str(listing["mls_number"] or listing["listing_id"])
        listing["address"] = listing["address"] or "Address unavailable"
        return listing


# Compatibility names for existing integrations and tests.
BridgeClient = ResoClient
BridgeError = ResoError
