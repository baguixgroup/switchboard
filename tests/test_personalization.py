"""Policy, privacy, persistence and real companion integration; no live AI calls."""
import asyncio
import json

import httpx
import pytest

from switchboard.personalization import (Appearance, AppearanceIn, CharacterBehavior, DecisionIn,
    DeterministicInference, Personalization, PreferenceIn, ProfileConflict, ProfilePatch, Settings)
from switchboard.store import Store
from switchboard.web.app import create_app
from .test_studio import make_engine
from .test_laith_crews import wait_chat


@pytest.fixture
def profile(tmp_path):
    store=Store(tmp_path/'profile.db')
    yield Personalization(store)
    store._db.close()


def settings(p,**changes):
    return p.update(ProfilePatch(revision=p.read()['revision'],settings=Settings(**(p.read()['settings']|changes))))


def choose(p,key='responseLength',value='concise',locked=False):
    return p.set_preference(PreferenceIn(revision=p.read()['revision'],key=key,value=value,locked=locked))


def learn(p,count=4):
    for n in range(count):p.observe('Please keep it brief.',f'turn-{n}',p.snapshot())


def test_consent_off_and_learning_separate_from_reflection(profile):
    p=profile;learn(p)
    assert not p.read()['evidence'] and not p.overview()['suggestions']
    choose(p)
    assert p.snapshot()['preferences']=={}
    settings(p,reflectPersonality=True)
    assert p.snapshot()['preferences']=={'responseLength':'concise'}
    assert not p.snapshot()['learningAuthorized']


def test_repeated_distinct_signals_threshold_and_no_raw_content(profile):
    p=profile;settings(p,learnPreferences=True)
    learn(p,3);assert not p.overview()['suggestions']
    p.observe('Please keep it brief.','turn-2',p.snapshot());assert not p.overview()['suggestions']
    p.observe('Please keep it brief.','turn-3',p.snapshot())
    s=p.overview()['suggestions'][0]
    assert s['confidence']==.9 and len(s['evidenceRefs'])==4
    assert 'keep it brief' not in json.dumps(p.read())
    assert 'evidence' not in p.overview() and 'epoch' not in p.overview()
    p.decide(s['id'],DecisionIn(revision=p.read()['revision'],approve=True))
    assert p.read()['preferences']['responseLength']['source']=='inferred'
    assert p.snapshot()['preferences']=={}  # learning never implicitly enables application


@pytest.mark.parametrize('depth,count',[('minimal',4),('balanced',3),('deep',2)])
def test_depth_requires_multiple_signals(profile,depth,count):
    settings(profile,learnPreferences=True,personalizationDepth=depth)
    learn(profile,count-1);assert not profile.overview()['suggestions']
    profile.observe('Be concise.','final',profile.snapshot());assert len(profile.overview()['suggestions'])==1


def test_explicit_choices_override_inference_and_correction_removes_candidates(profile):
    p=profile;settings(p,learnPreferences=True,reflectPersonality=True)
    learn(p);choose(p,value='detailed',locked=True);learn(p)
    assert p.snapshot()['preferences']['responseLength']=='detailed' and not p.overview()['suggestions']
    choose(p,value='balanced',locked=False)
    assert p.snapshot()['preferences']['responseLength']=='balanced'
    assert p.read()['preferences']['responseLength']['source']=='explicit'


def test_rejection_suppression_and_deletion_do_not_relearn(profile):
    p=profile;settings(p,learnPreferences=True);learn(p)
    s=p.overview()['suggestions'][0]
    p.decide(s['id'],DecisionIn(revision=p.read()['revision'],approve=False))
    for n in range(5):p.observe('Be concise.','new-'+str(n),p.snapshot())
    assert not p.overview()['suggestions']
    choose(p);p.delete_preference('responseLength',p.read()['revision'])
    for n in range(6):p.observe('Be detailed.','later-'+str(n),p.snapshot())
    assert not p.read()['preferences'] and not p.read()['evidence'] and not p.overview()['suggestions']


def test_private_mode_revokes_prepared_learning_and_preserves_stored_choices(profile):
    p=profile;choose(p);settings(p,learnPreferences=True,reflectPersonality=True)
    old=p.snapshot();settings(p,privateMode=True)
    p.observe('Be concise.','old-turn',old)
    assert not p.read()['evidence'] and not p.snapshot()['preferences'] and not p.snapshot()['learningAuthorized']
    assert p.read()['preferences']
    settings(p,privateMode=False)
    p.observe('Be concise.','old-turn',old);assert not p.read()['evidence']


def test_reset_restores_defaults_and_invalidates_old_signals_after_reconsent(profile):
    p=profile;settings(p,learnPreferences=True);old=p.snapshot();choose(p)
    revision=p.read()['revision'];p.reset(revision)
    assert p.read()['revision']>revision and not p.read()['preferences']
    assert p.read()['settings']==Settings().model_dump()
    settings(p,learnPreferences=True)
    p.observe('Be concise.','old-turn',old);assert not p.read()['evidence']


def test_profession_and_presentation_are_independent_and_never_enable_permissions(profile):
    p=profile;p.update(ProfilePatch(revision=0,roles=['developer','nurse'],activeRole='nurse'))
    assert p.snapshot()['role']==''
    settings(p,professionMirroring=True)
    assert p.snapshot()['role']=='nurse'
    assert p.read()['appearance']['presentation']=='default'
    p.request_appearance(AppearanceIn(revision=p.read()['revision'],appearance=Appearance(presentation='masculine',outfit='profession')))
    assert p.read()['appearance']['presentation']=='default'
    s=p.overview()['suggestions'][0]
    p.decide(s['id'],DecisionIn(revision=p.read()['revision'],approve=True))
    view=p.overview()['presentation'];assert view['fallback'] and 'outfit:nurse' in view['missingAssets']
    assert view['image']=='/static/laith.png'
    settings(p,professionMirroring=False)
    assert p.read()['appearance']['presentation']=='masculine' and p.snapshot()['role']==''
    assert not {'tools','permissions'}&p.read().keys()


def test_stale_approval_and_concurrent_edits_are_rejected(profile):
    p=profile;p.request_appearance(AppearanceIn(revision=0,appearance=Appearance(accessory='book')))
    stale=p.overview();choose(p)
    with pytest.raises(ProfileConflict):p.decide(stale['suggestions'][0]['id'],DecisionIn(revision=stale['revision'],approve=True))
    assert p.read()['appearance']['accessory']=='none'
    with pytest.raises(ProfileConflict):p.update(ProfilePatch(revision=0,roles=[]))


def test_appearance_off_private_and_explicit_opt_out_of_ask(profile):
    p=profile;settings(p,askBeforeAppearanceChanges=False)
    p.request_appearance(AppearanceIn(revision=p.read()['revision'],appearance=Appearance(accessory='glasses')))
    assert p.read()['appearance']['accessory']=='glasses' and not p.overview()['suggestions']
    settings(p,appearanceAdaptation='off')
    assert not p.overview()['presentation']['missingAssets']
    settings(p,privateMode=True)
    with pytest.raises(ValueError):p.request_appearance(AppearanceIn(revision=p.read()['revision'],appearance=Appearance()))


def test_persistence_restart(profile):
    choose(profile,locked=True)
    reopened=Personalization(profile.store)
    assert reopened.overview()==profile.overview()


def test_inference_is_allowlisted_not_document_or_sensitive_profiling(profile):
    inference=DeterministicInference()
    for message in ['I am a nurse', 'I am depressed', 'Copied text (reference material):\nBe concise.', '"Be concise."', 'Do not be concise', 'Write a story about shorter answers']:
        assert inference.extract(message)==[]
    class Unsafe:
        def extract(self,message):return [('tools','all')]
    profile.inference=Unsafe();settings(profile,learnPreferences=True);learn(profile)
    assert not profile.read()['evidence']


def test_behavior_priorities_quiet_cancel_and_unsupported_states():
    b=CharacterBehavior();assert not b.transition('roaming')
    b.configure(Settings(roamingActivity='low').model_dump());assert b.transition('roaming')
    assert b.transition('working') and not b.transition('roaming') and not b.transition('sleeping')
    b.cancel();assert b.state=='idle'
    b.configure(Settings(quietMode=True,roamingActivity='balanced').model_dump())
    assert b.state=='resting' and not b.transition('roaming')
    assert not b.transition('observing') and not b.transition('speaking')
    b.transition('working');b.cancel();assert b.state=='resting'
    with pytest.raises(ValueError):b.transition('unknown')


@pytest.mark.parametrize('platform,expected',[('macos',True),('windows',True),('linux',False)])
def test_native_platform_gate_keeps_pi_headless(platform,expected,monkeypatch):
    from switchboard import desktop_pet
    monkeypatch.setattr(desktop_pet,'os_family',lambda:platform)
    monkeypatch.setattr(desktop_pet,'helper_path',lambda:None)
    monkeypatch.setattr(desktop_pet.importlib.util,'find_spec',lambda name:object())
    assert desktop_pet.available() is expected


async def test_authenticated_api_validation_revision_and_reset(tmp_path):
    e=make_engine(tmp_path);app=create_app(e)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app,client=('203.0.113.5',1)),base_url='http://test') as c:
        assert (await c.get('/api/laith/personalization')).status_code==401
        c.headers['Authorization']='Bearer test-token'
        p=(await c.get('/api/laith/personalization')).json()
        assert len(p['professions'])==10
        assert (await c.post('/api/laith/personalization',json={'revision':0,'roles':['administrator']})).status_code==400
        assert (await c.post('/api/laith/personalization',json={'revision':0,'settings':{'learnPreferences':'true'}})).status_code==422
        assert (await c.post('/api/laith/personalization',json={'revision':0,'tools':'all'})).status_code==422
        assert (await c.post('/api/laith/personalization/preferences',json={'revision':0,'key':'tone','value':'calm','locked':True})).status_code==200
        assert (await c.post('/api/laith/personalization/reset',json={'revision':0})).status_code==409
        assert (await c.post('/api/laith/personalization/reset',json={'revision':1})).json()['settings']['learnPreferences'] is False
    await e.close()


async def test_companion_snapshots_style_rejects_stale_and_learns_only_on_send(tmp_path):
    e=make_engine(tmp_path);c=e.companion;p=c.personalization
    settings(p,learnPreferences=True,reflectPersonality=True,personalizationDepth='deep');choose(p,key='tone',value='calm')
    chat=c.create_chat();turn=await c.prepare(chat['id'],'Be concise.',model='mock:mock-small')
    raw=c.turns(chat['id'])[0];assert '"tone": "calm"' in raw['prompt']
    assert not p.read()['evidence']
    choose(p,key='tone',value='direct')
    with pytest.raises(ValueError,match='Personalization changed'):await c.send(chat['id'],turn['id'])
    for _ in range(2):
        turn=await c.prepare(chat['id'],'Be concise.',model='mock:mock-small')
        await c.send(chat['id'],turn['id']);await wait_chat(c,chat['id'])
    assert len(p.overview()['suggestions'])==1
    settings(p,privateMode=True)
    turn=await c.prepare(chat['id'],'Hello',model='mock:mock-small')
    assert turn['personalization']['preferences']=={}
    assert turn['tools_enabled'] is False and turn['plan']['billing']=='plans_only'
    await e.close()


async def test_retry_rebinds_evidence_revision_without_learning_again(tmp_path,monkeypatch):
    from switchboard.providers import ProviderError
    e=make_engine(tmp_path);c=e.companion;p=c.personalization
    settings(p,learnPreferences=True)
    async def fail(*args):raise ProviderError('test failure')
    monkeypatch.setattr(e.providers['mock'],'run',fail)
    chat=c.create_chat();turn=await c.prepare(chat['id'],'Be concise.',model='mock:mock-small')
    await c.send(chat['id'],turn['id']);assert (await wait_chat(c,chat['id']))['turns'][-1]['status']=='failed'
    assert p.read()['revision']>turn['personalization']['revision']
    retry=await c.retry_direct(chat['id'],turn['id'])
    await c.send(chat['id'],retry['id']);await wait_chat(c,chat['id'])
    assert len(p.read()['evidence']['responseLength:concise'])==1
    settings(p,reflectPersonality=True);choose(p,key='tone',value='direct')
    with pytest.raises(ValueError,match='personality changed'):await c.retry_direct(chat['id'],retry['id'])
    await e.close()
