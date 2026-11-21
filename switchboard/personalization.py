"""Consent-gated Laith preferences. No model, GUI or OS dependency.

One profile per hub, not per authenticated human. Raw messages are inspected only
at an authorized send and are never copied into the profile/evidence store.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass
from importlib import resources
import json
from itertools import islice
import re
import time
from typing import Literal
import uuid

from pydantic import BaseModel, ConfigDict, Field, StrictBool


TRAITS = {
    'responseLength': ['concise', 'balanced', 'detailed'],
    'tone': ['neutral', 'warm', 'direct', 'playful', 'calm'],
}
ROLES = {
    'nurse': ('Nurse', 'Be organized, supportive and clear.'),
    'doctor': ('Doctor', 'Be precise, evidence-aware and clear about uncertainty.'),
    'developer': ('Developer', 'Use practical technical explanations and reproducible steps.'),
    'teacher': ('Teacher', 'Explain patiently with examples and checks for understanding.'),
    'researcher': ('Researcher', 'Distinguish evidence, uncertainty and open questions.'),
    'designer': ('Designer', 'Explain visual choices, alternatives and concrete tradeoffs.'),
    'business': ('Business professional', 'Organize decisions around goals and practical outcomes.'),
    'student': ('Student', 'Support learning with examples and manageable steps.'),
    'coach': ('Athlete / Coach', 'Be encouraging and use clear, manageable goals.'),
    'custom': ('Neutral / everyday', 'Use a neutral, general-purpose assistant style without an occupational persona.'),
}


class Settings(BaseModel):
    model_config = ConfigDict(extra='forbid')
    learnPreferences: StrictBool = False
    reflectPersonality: StrictBool = False
    professionMirroring: StrictBool = False
    askBeforeAppearanceChanges: StrictBool = True
    personalizationDepth: Literal['minimal', 'balanced', 'deep'] = 'minimal'
    roamingActivity: Literal['off', 'low', 'balanced'] = 'off'
    appearanceAdaptation: Literal['off', 'suggest', 'approved'] = 'suggest'
    moodExpressiveness: Literal['off', 'low', 'medium'] = 'low'
    quietMode: StrictBool = False
    privateMode: StrictBool = False


class Appearance(BaseModel):
    model_config = ConfigDict(extra='forbid')
    presentation: Literal['default', 'masculine', 'feminine', 'neutral', 'custom'] = 'default'
    outfit: Literal['default', 'profession'] = 'default'
    accessory: Literal['none', 'glasses', 'headphones', 'book'] = 'none'


class ProfilePatch(BaseModel):
    model_config = ConfigDict(extra='forbid')
    revision: int = Field(ge=0, strict=True)
    settings: Settings | None = None
    roles: list[str] | None = Field(default=None, max_length=10)
    activeRole: str | None = None


class RevisionIn(BaseModel):
    model_config = ConfigDict(extra='forbid')
    revision: int = Field(ge=0, strict=True)


class PreferenceIn(RevisionIn):
    key: str
    value: str
    locked: StrictBool = False


class AppearanceIn(RevisionIn):
    appearance: Appearance


class DecisionIn(RevisionIn):
    approve: StrictBool


class RoleLookIn(RevisionIn):
    role: str
    confirm: StrictBool = False
    useStyle: StrictBool = True


class StyleIn(RevisionIn):
    tone: Literal['neutral', 'warm', 'direct', 'playful', 'calm']
    responseLength: Literal['concise', 'balanced', 'detailed']


def style_instructions(snapshot):
    """Concrete communication instructions, never credentials or permissions."""
    traits = snapshot['preferences']
    lines = []
    lengths = {'concise': 'Keep replies brief: normally one short paragraph or up to three bullets. Expand when the user asks or accuracy requires it.',
               'balanced': 'Use a clear direct answer followed by a moderate amount of useful explanation.',
               'detailed': 'Give thorough explanations, practical examples and clearly organized steps when helpful.'}
    tones = {'neutral': 'Use a neutral, even-handed, matter-of-fact voice. Avoid roleplay, hype, emotional mirroring and occupational assumptions.',
             'warm': 'Speak warmly and supportively, using natural, encouraging language.',
             'direct': 'Be direct, candid and practical. Skip greetings and unnecessary reassurance.',
             'playful': 'Use a light, gently humorous voice when appropriate; remain clear and respectful.',
             'calm': 'Use a calm, steady, patient voice. Avoid hype and excessive exclamation marks.'}
    if traits.get('responseLength') in lengths: lines.append(lengths[traits['responseLength']])
    if traits.get('tone') in tones: lines.append(tones[traits['tone']])
    if snapshot['role']:
        lines.append('Selected professional context: '+ROLES[snapshot['role']][0]+'. '+snapshot['roleStyle'])
    return '\n'.join(lines)


class ProfileConflict(ValueError):
    pass


def fresh():
    return {'version': 1, 'revision': 0, 'epoch': uuid.uuid4().hex,
            'settings': Settings().model_dump(), 'roles': [], 'activeRole': '',
            'appearance': Appearance().model_dump(), 'preferences': {},
            'suggestions': [], 'evidence': {}, 'suppressed': []}


def assets():
    return json.loads(resources.files('switchboard').joinpath('web', 'static', 'laith-assets.json').read_text())


def presentation(profile, requested=None):
    """Requested metadata and available presentation are deliberately separate."""
    settings = profile['settings']
    requested = requested if requested is not None else profile['appearance']
    enabled = not settings['privateMode']
    active = requested if enabled else Appearance().model_dump()
    # An explicitly selected outfit is not an inferred profession. Disabling
    # automatic mirroring must not discard the user's manual wardrobe choice.
    role = profile['activeRole'] if active['outfit'] == 'profession' else ''
    manifest = assets()
    outfit = manifest['outfits'].get(role) if isinstance(manifest['outfits'], dict) else None
    image = manifest['defaultImage']
    missing = []
    if active['presentation'] not in manifest['presentations']: missing.append('presentation:' + active['presentation'])
    if role:
        if outfit and resources.files('switchboard').joinpath('web','static',outfit['image']).is_file(): image = outfit['image']
        else: missing.append('outfit:' + role)
    if active['accessory'] != 'none' and active['accessory'] not in (outfit or {}).get('accessories', []):
        missing.append('accessory:' + active['accessory'])
    return {'identity': 'Laith · Atlas lion', 'requested': requested,
            'activeRole': role, 'roleName': ROLES[role][0] if role in ROLES else '',
            'professionStyleEnabled': settings['professionMirroring'] and not settings['privateMode'],
            'form': 'standing companion' if image != manifest['defaultImage'] else 'classic lion',
            'image': '/static/'+image, 'missingAssets': missing,
            'fallback': bool(missing), 'mood': 'calm',
            'motionEnabled': not (settings['quietMode'] or settings['privateMode'])}


@dataclass
class CharacterBehavior:
    """Local state, not a hub-global animation. No recording or screen reading."""
    state: str = 'idle'
    quiet: bool = False
    roaming: bool = False
    STATES = ('idle', 'roaming', 'observing', 'listening', 'speaking', 'working', 'reacting', 'resting', 'sleeping')
    SUPPORTED = ('idle', 'roaming', 'working', 'reacting', 'resting', 'sleeping')

    @property
    def mood(self):
        return {'working': 'focused', 'reacting': 'happy', 'sleeping': 'sleepy'}.get(self.state, 'calm')

    def configure(self, settings):
        self.quiet = settings['quietMode'] or settings['privateMode']
        self.roaming = settings['roamingActivity'] != 'off' and not self.quiet
        if self.quiet and self.state != 'working': self.state = 'resting'
        elif self.state == 'roaming' and not self.roaming: self.state = 'idle'
        elif self.state == 'resting' and not self.quiet: self.state = 'idle'

    def transition(self, target, interrupt=False):
        if target not in self.STATES: raise ValueError('Unknown character state.')
        if target not in self.SUPPORTED: return False
        if target == 'roaming' and (not self.roaming or self.quiet): return False
        if self.quiet and target in ('idle', 'roaming', 'reacting'): target = 'resting'
        if self.state == 'working' and target in ('roaming', 'sleeping', 'resting') and not interrupt: return False
        if self.state == 'sleeping' and target == 'roaming': return False
        self.state = target
        return True

    def cancel(self):
        self.state = 'resting' if self.quiet else 'idle'


class DeterministicInference:
    """Small allowlist; never infers profession, gender, health or permissions."""
    def extract(self, message):
        # Only a complete, direct style request qualifies, not copied documents,
        # quotes, code blocks or incidental mentions in a larger conversation.
        normalized = message.strip().lower().rstrip('.!')
        for pattern, value in [
            (r'(?:please )?(?:keep (?:it|answers|your answers) (?:brief|short)|shorter answers|be concise)', 'concise'),
            (r'(?:please )?(?:give (?:me )?detailed answers|more detailed answers|be detailed)', 'detailed'),
        ]:
            if re.fullmatch(pattern, normalized): return [('responseLength', value)]
        return []


class Personalization:
    def __init__(self, store, inference=None):
        self.store = store
        self.inference = inference or DeterministicInference()
        store._exec('CREATE TABLE IF NOT EXISTS laith_profile (id INTEGER PRIMARY KEY CHECK(id=1),payload TEXT NOT NULL)')
        store._exec('INSERT OR IGNORE INTO laith_profile VALUES(1,?)', (json.dumps(fresh()),))

    def read(self):
        return json.loads(self.store._exec('SELECT payload FROM laith_profile WHERE id=1')[0]['payload'])

    def _mutate(self, revision, change):
        with self.store.transaction() as db:
            profile = json.loads(db.execute('SELECT payload FROM laith_profile WHERE id=1').fetchone()['payload'])
            if revision is not None and profile['revision'] != revision:
                raise ProfileConflict('Personalization changed. Reload and review before saving.')
            before = copy.deepcopy(profile)
            change(profile)
            if profile != before:
                profile['revision'] += 1
                db.execute('UPDATE laith_profile SET payload=? WHERE id=1', (json.dumps(profile),))
        return self.overview(profile)

    def overview(self, profile=None):
        profile = profile if profile is not None else self.read()
        manifest=assets()
        return {k: copy.deepcopy(v) for k, v in profile.items() if k not in ('evidence', 'suppressed', 'epoch')} | {
            'suggestions': [dict(s,preview=presentation(profile,s['appearance'])) if s['kind']=='appearance' else copy.deepcopy(s) for s in profile['suggestions']],
            'professions': [{'id': k, 'name': v[0], 'style': v[1],
                'outfitAvailable': k in manifest['outfits'],
                'image': '/static/'+manifest['outfits'][k]['image'] if k in manifest['outfits'] else '/static/laith.png'} for k, v in ROLES.items()],
            'traitChoices': TRAITS, 'presentation': presentation(profile), 'assets': manifest,
            'behavior': {'states': CharacterBehavior.STATES, 'supported': CharacterBehavior.SUPPORTED,
                         'fallback': 'Default lion and activity labels; no voice or sensor states.'}}

    def update(self, patch):
        def change(p):
            if patch.roles is not None:
                if len(set(patch.roles)) != len(patch.roles) or any(r not in ROLES for r in patch.roles):
                    raise ValueError('Choose unique professions from the registry.')
                p['roles'] = patch.roles
                if p['activeRole'] not in p['roles']: p['activeRole'] = ''
            if patch.activeRole is not None:
                if patch.activeRole and patch.activeRole not in p['roles']: raise ValueError('Select this profession first.')
                p['activeRole'] = patch.activeRole
            if patch.settings is not None:
                settings = patch.settings.model_dump()
                if settings != p['settings']:
                    # Revoking consent invalidates prepared signals and suggestions.
                    if settings['privateMode'] or not settings['learnPreferences']:
                        p['evidence'] = {}
                        p['suggestions'] = [s for s in p['suggestions'] if s['kind'] != 'preference']
                    if settings['privateMode']:p['suggestions'] = []
                    p['epoch'] = uuid.uuid4().hex
                p['settings'] = settings
        return self._mutate(patch.revision, change)

    @staticmethod
    def validate_trait(key, value):
        if key not in TRAITS or value not in TRAITS[key]: raise ValueError('Choose a supported preference value.')

    def set_preference(self, request):
        self.validate_trait(request.key, request.value)
        def change(p):
            now = time.time()
            old = p['preferences'].get(request.key, {})
            p['preferences'][request.key] = {'key': request.key, 'value': request.value, 'source': 'explicit',
                'locked': request.locked, 'confidence': 1.0, 'evidenceRefs': [],
                'created': old.get('created', now), 'updated': now}
            self._forget_candidates(p, request.key)
        return self._mutate(request.revision, change)

    @staticmethod
    def _forget_candidates(p, key):
        p['suggestions'] = [s for s in p['suggestions'] if s.get('key') != key]
        p['evidence'] = {k: v for k, v in p['evidence'].items() if not k.startswith(key + ':')}

    def delete_preference(self, key, revision):
        if key not in TRAITS: raise ValueError('Unknown preference.')
        def change(p):
            p['preferences'].pop(key, None)
            self._forget_candidates(p, key)
            if key not in p['suppressed']: p['suppressed'].append(key)
        return self._mutate(revision, change)

    def request_appearance(self, request):
        def change(p):
            if p['settings']['privateMode']: raise ValueError('Leave private mode before changing adaptive appearance.')
            desired = request.appearance.model_dump()
            p['suggestions'] = [s for s in p['suggestions'] if s['kind'] != 'appearance']
            if desired == p['appearance']: return
            if p['settings']['askBeforeAppearanceChanges']:
                p['suggestions'].append({'id': uuid.uuid4().hex, 'kind': 'appearance', 'appearance': desired,
                                        'created': time.time(), 'baseRevision': p['revision'] + 1})
            else: p['appearance'] = desired
        return self._mutate(request.revision, change)

    def apply_role(self, request):
        if request.role not in ROLES: raise ValueError('Choose a supported profession.')
        if not request.confirm: raise ValueError('Confirm the displayed outfit and profession style first.')
        def change(p):
            if p['settings']['privateMode']: raise ValueError('Leave private mode before changing appearance.')
            if request.role not in p['roles']: p['roles'].append(request.role)
            p['activeRole'] = request.role
            p['appearance']['outfit'] = 'profession'
            p['settings']['professionMirroring'] = request.useStyle
            p['suggestions'] = [s for s in p['suggestions'] if s['kind'] != 'appearance']
        return self._mutate(request.revision, change)

    def apply_style(self, request):
        def change(p):
            if p['settings']['privateMode']: raise ValueError('Leave private mode before applying a personality style.')
            now=time.time()
            for key in ('tone','responseLength'):
                old=p['preferences'].get(key,{})
                p['preferences'][key]={'key':key,'value':getattr(request,key),'source':'explicit','locked':True,
                    'confidence':1.0,'evidenceRefs':[],'created':old.get('created',now),'updated':now}
                self._forget_candidates(p,key)
            p['settings']['reflectPersonality']=True
        return self._mutate(request.revision, change)

    def decide(self, suggestion_id, request):
        def change(p):
            suggestion = next((s for s in p['suggestions'] if s['id'] == suggestion_id), None)
            if not suggestion: raise ValueError('Suggestion is no longer available.')
            if request.approve:
                if p['settings']['privateMode']: raise ValueError('Leave private mode before applying an adaptation.')
                if suggestion['kind'] == 'appearance': p['appearance'] = suggestion['appearance']
                else:
                    if not p['settings']['learnPreferences']: raise ValueError('Learning is disabled.')
                    key = suggestion['key']; old = p['preferences'].get(key)
                    if old and (old['locked'] or old['source'] == 'explicit'): raise ValueError('An explicit preference takes precedence.')
                    now = time.time()
                    p['preferences'][key] = {k: suggestion[k] for k in ('key', 'value', 'confidence', 'evidenceRefs')} | {
                        'source': 'inferred', 'locked': False, 'created': old.get('created', now) if old else now, 'updated': now}
            elif suggestion['kind'] == 'preference':
                blocked = suggestion['key'] + ':' + suggestion['value']
                if blocked not in p['suppressed']: p['suppressed'].append(blocked)
            p['suggestions'] = [s for s in p['suggestions'] if s['id'] != suggestion_id]
        return self._mutate(request.revision, change)

    def reset(self, revision):
        def change(p):
            previous_revision = p['revision']
            p.clear();p.update(fresh());p['revision'] = previous_revision
        return self._mutate(revision, change)

    def snapshot(self):
        p = self.read();settings = p['settings']
        preferences = {k: v['value'] for k, v in p['preferences'].items()} if settings['reflectPersonality'] and not settings['privateMode'] else {}
        role = p['activeRole'] if settings['professionMirroring'] and not settings['privateMode'] else ''
        return {'revision': p['revision'], 'epoch': p['epoch'],
                'learningAuthorized': settings['learnPreferences'] and not settings['privateMode'],
                'preferences': preferences, 'role': role, 'roleStyle': ROLES[role][1] if role else ''}

    def observe(self, message, turn_id, snapshot):
        """Called once on dispatch, never on preview, retry, polling or old history."""
        if not snapshot.get('learningAuthorized'): return
        try:
            candidates = list(islice(self.inference.extract(message), 8))
            for key, value in candidates:self.validate_trait(key, value)
        except Exception:
            # Replaceable inference is untrusted. A failure must not turn an
            # already dispatched conversation into an apparent send failure.
            return
        def change(p):
            settings = p['settings']
            if not settings['learnPreferences'] or settings['privateMode'] or p['epoch'] != snapshot.get('epoch'): return
            for key, value in candidates:
                candidate = key + ':' + value
                old = p['preferences'].get(key)
                if key in p['suppressed'] or candidate in p['suppressed']: continue
                if old and (old['locked'] or old['source'] == 'explicit' or old['value'] == value): continue
                refs = p['evidence'].setdefault(candidate, [])
                if turn_id not in refs: refs.append(turn_id)
                del refs[:-8]
                threshold = {'minimal': 4, 'balanced': 3, 'deep': 2}[settings['personalizationDepth']]
                if len(refs) < threshold: continue
                if any(s.get('key') == key for s in p['suggestions']): continue
                p['suggestions'].append({'id': uuid.uuid4().hex, 'kind': 'preference', 'key': key, 'value': value,
                    'confidence': min(.95, .5 + .1 * len(refs)), 'evidenceRefs': refs[:], 'created': time.time()})
        self._mutate(None, change)
