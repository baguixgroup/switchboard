"""Exercise the actual native widgets offscreen, without touching the user's clipboard."""
import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import time
import pytest

qt=pytest.importorskip('PySide6')
from PySide6.QtWidgets import QApplication, QMessageBox
from switchboard.config import Config
from .conftest import make_home
from switchboard import desktop_pet_ui as ui


@pytest.fixture(scope='module')
def app():
    instance=QApplication.instance() or QApplication([])
    yield instance


class FakeClient:
    def __init__(self,*a):self.calls=[];self.message='';self.tools=False
    def overview(self):return {'models':[],'library':{'chats':[],'abilities':[{'id':'conversation','name':'Everyday companion'}]}}
    def new_chat(self):return {'id':'chat-test'}
    def prepare(self,key,message,model,ability,tools,knowledge):
        self.calls.append('prepare');self.message=message;self.tools=tools
        return {'id':'turn-test','plan':{'model_label':'Test plan','needs_confirmation':tools},'tools_enabled':tools,'sources':[],'memory_count':0}
    def send(self,*a):self.calls.append('send');return {'status':'queued'}
    def chat(self,key):return {'id':key,'turns':[{'id':'turn-test','message':self.message,'answer':'A useful revised paragraph.','status':'done','plan':{'model_label':'Test plan'},'task_id':'task-test'}]}


def settle(app,predicate):
    deadline=time.monotonic()+4
    while time.monotonic()<deadline:
        app.processEvents()
        if predicate():return
        time.sleep(.01)
    pytest.fail('Native widget did not reach expected state.')


def test_native_animation_clipboard_preview_reply_copy_and_visibility(app,tmp_path,monkeypatch):
    monkeypatch.setattr(ui,'PetClient',FakeClient);cfg=Config(make_home(tmp_path));lion=ui.Lion(cfg);client=lion.panel.client
    settle(app,lambda:lion.panel.ability.count()>0)
    assert len(lion.frames)==16 and len({f.cacheKey() for f in lion.frames})==16
    assert all(f.hasAlphaChannel() for f in lion.frames)
    assert not lion.mask().isEmpty()
    lion.show_pet();before=lion.frame;lion.advance();assert lion.frame!=before
    lion.action('pause');before=lion.frame;lion.advance();assert lion.frame==before
    app.clipboard().setText('Text copied from another app.');assert not lion.panel.message.toPlainText() and not client.calls
    lion.panel.use_clipboard();assert 'Text copied from another app.' in lion.panel.message.toPlainText() and not client.calls
    lion.panel.message.setPlainText('Improve this:\n'+lion.panel.message.toPlainText());lion.panel.prepare()
    settle(app,lambda:lion.panel.latest_answer!='')
    assert client.calls==['prepare','send'] and not client.tools
    assert 'Text copied from another app.' in client.message
    lion.panel.copy_answer();assert app.clipboard().text()=='A useful revised paragraph.'
    lion.show_panel();assert lion.panel.isVisible()
    lion.action('hide');assert not lion.isVisible() and not lion.panel.isVisible()
    lion.panel.timer.stop();lion.animation.stop();lion.panel.deleteLater();lion.deleteLater();app.processEvents()


def test_native_tool_confirmation_can_be_cancelled_without_sending(app,tmp_path,monkeypatch):
    monkeypatch.setattr(ui,'PetClient',FakeClient);monkeypatch.setattr(QMessageBox,'exec',lambda self:QMessageBox.StandardButton.Cancel)
    lion=ui.Lion(Config(make_home(tmp_path)));panel=lion.panel
    settle(app,lambda:panel.ability.count()>0)
    panel.tools.setChecked(True);panel.message.setPlainText('Use connected tools to help with my task.');panel.prepare()
    settle(app,lambda:panel.client.calls==['prepare'] and not panel.busy)
    assert panel.message.toPlainText() and 'send' not in panel.client.calls
    panel.timer.stop();lion.animation.stop();panel.deleteLater();lion.deleteLater();app.processEvents()


def test_native_panel_recovers_from_initial_hub_outage_without_losing_draft(app,tmp_path,monkeypatch):
    class ReconnectingClient(FakeClient):
        def __init__(self,*a):super().__init__();self.connections=0
        def overview(self):
            self.connections+=1
            if self.connections==1:raise ValueError('temporary outage')
            return super().overview()
    monkeypatch.setattr(ui,'PetClient',ReconnectingClient)
    lion=ui.Lion(Config(make_home(tmp_path)));panel=lion.panel;panel.message.setPlainText('Keep this unsent draft.')
    settle(app,lambda:'Retrying automatically' in panel.connection.text())
    assert not panel.send.isEnabled() and not panel.client.calls
    panel.connect_hub();settle(app,lambda:panel.metadata_loaded)
    assert panel.send.isEnabled() and panel.ability.count()==1
    assert panel.message.toPlainText()=='Keep this unsent draft.' and not panel.reconnect_timer.isActive()
    panel.timer.stop();panel.reconnect_timer.stop();lion.animation.stop();panel.deleteLater();lion.deleteLater();app.processEvents()


def test_nonactivating_pet_opens_options_on_right_mouse_press(app,tmp_path,monkeypatch):
    from PySide6.QtCore import Qt, QPoint
    from PySide6.QtTest import QTest
    monkeypatch.setattr(ui,'PetClient',FakeClient)
    lion=ui.Lion(Config(make_home(tmp_path)));positions=[]
    monkeypatch.setattr(lion,'open_menu',positions.append)
    settle(app,lambda:lion.panel.metadata_loaded)
    lion.show_pet();QTest.mouseClick(lion,Qt.MouseButton.RightButton,pos=QPoint(90,90))
    assert len(positions)==1 and not lion.panel.isVisible()
    QTest.mouseClick(lion,Qt.MouseButton.LeftButton,pos=QPoint(90,90))
    assert lion.panel.isVisible()
    lion.panel.timer.stop();lion.animation.stop();lion.panel.deleteLater();lion.deleteLater();app.processEvents()


def test_chat_settings_are_hidden_and_enter_sends_while_shift_enter_adds_line(app,tmp_path,monkeypatch):
    from PySide6.QtTest import QTest
    from PySide6.QtCore import Qt
    monkeypatch.setattr(ui,'PetClient',FakeClient);lion=ui.Lion(Config(make_home(tmp_path)));panel=lion.panel
    settle(app,lambda:panel.metadata_loaded);lion.show_panel()
    assert not panel.settings.isVisible() and not panel.model.isVisible() and not panel.tools.isVisible()
    app.processEvents();assert panel.message.height()<70
    panel.message.setPlainText('Hello');QTest.keyClick(panel.message,Qt.Key.Key_Return,Qt.KeyboardModifier.ShiftModifier)
    assert '\n' in panel.message.toPlainText() and not panel.client.calls
    QTest.keyClick(panel.message,Qt.Key.Key_Return);settle(app,lambda:panel.latest_answer!='')
    assert panel.client.calls==['prepare','send']
    panel.open_settings();assert panel.settings.isVisible() and panel.model.isVisible()
    panel.settings.close();panel.timer.stop();lion.animation.stop();panel.deleteLater();lion.deleteLater();app.processEvents()


def test_waiting_app_is_not_labelled_thinking_and_can_be_recovered(app,tmp_path,monkeypatch):
    class RecoveryClient(FakeClient):
        def retry(self,key,turn):
            self.calls.append('retry');return {'id':'recovered','message':'Hello','plan':{'model_label':'Connected plan','needs_confirmation':False}}
        def chat(self,key):
            return {'id':key,'turns':[{'id':'recovered','message':'Hello','answer':'Direct reply.','status':'done','plan':{'model_label':'Connected plan'}}]}
    monkeypatch.setattr(ui,'PetClient',RecoveryClient);lion=ui.Lion(Config(make_home(tmp_path)));panel=lion.panel
    settle(app,lambda:panel.metadata_loaded)
    panel.show_chat({'id':'chat-test','turns':[{'id':'manual','message':'Hello','answer':'Open ChatGPT','status':'waiting','task_id':'manual-task','plan':{}}]})
    assert 'Waiting for another app' in panel.subtitle.text() and panel.recover.text()=='Reply here instead'
    assert 'thinking' not in panel.transcript.toPlainText().lower()
    panel.recover_request();settle(app,lambda:panel.latest_answer=='Direct reply.')
    assert panel.client.calls==['retry','send'] and not panel.busy
    panel.timer.stop();lion.animation.stop();panel.deleteLater();lion.deleteLater();app.processEvents()


def test_delivery_timeout_reconciles_accepted_request_without_duplicate_send(app,tmp_path,monkeypatch):
    class LostResponseClient(FakeClient):
        def send(self,*a):self.calls.append('send');raise ValueError('response lost after acceptance')
    monkeypatch.setattr(ui,'PetClient',LostResponseClient);lion=ui.Lion(Config(make_home(tmp_path)));panel=lion.panel
    settle(app,lambda:panel.metadata_loaded);panel.message.setPlainText('Hello');panel.prepare()
    settle(app,lambda:bool(panel.latest_answer))
    assert panel.client.calls==['prepare','send'] and not panel.message.toPlainText() and not panel.busy
    panel.timer.stop();lion.animation.stop();panel.deleteLater();lion.deleteLater();app.processEvents()


def test_native_markdown_displays_formatting_without_html_or_image_fetches(app):
    result=ui.reply_html('**Clear answer**\n\n<script>bad()</script>\n\n![private](file:///private/secret.png)\n\n![reference][image]\n\n[image]: file:///private/secret2.png')
    assert '<script>' not in result and '<img' not in result and 'Clear answer' in result


def test_recovered_paid_preview_can_be_declined_before_dispatch(app,tmp_path,monkeypatch):
    monkeypatch.setattr(ui,'PetClient',FakeClient);monkeypatch.setattr(QMessageBox,'exec',lambda self:QMessageBox.StandardButton.Cancel)
    lion=ui.Lion(Config(make_home(tmp_path)));panel=lion.panel;settle(app,lambda:panel.metadata_loaded)
    panel.message.setPlainText('Saved draft.');panel.confirm(('chat-test',{'id':'preview','plan':{'model_label':'API model','effective_cost_usd':.01}}))
    assert 'send' not in panel.client.calls and panel.message.toPlainText()=='Saved draft.'
    settle(app,lambda:not panel.polling)
    panel.timer.stop();lion.animation.stop();panel.deleteLater();lion.deleteLater();app.processEvents()


def test_finished_followup_does_not_show_an_old_failure_as_current(app,tmp_path,monkeypatch):
    monkeypatch.setattr(ui,'PetClient',FakeClient);lion=ui.Lion(Config(make_home(tmp_path)));panel=lion.panel
    settle(app,lambda:panel.metadata_loaded)
    panel.show_chat({'id':'chat-test','turns':[{'id':'old','message':'Earlier','status':'failed','error':'Old failure'}, {'id':'new','message':'Next','status':'done','answer':'Finished.'}]})
    assert not panel.retry_turn and not panel.busy and not panel.connection.text()
    panel.timer.stop();lion.animation.stop();panel.deleteLater();lion.deleteLater();app.processEvents()


def test_personalization_bounds_movement_and_quiet_mode_stops_animation(app,tmp_path,monkeypatch):
    from switchboard.personalization import fresh, presentation
    monkeypatch.setattr(ui,'PetClient',FakeClient)
    lion=ui.Lion(Config(make_home(tmp_path)));settle(app,lambda:lion.panel.metadata_loaded)
    p=fresh();p['settings'].update(roamingActivity='low',moodExpressiveness='off')
    lion.apply_personalization({'settings':p['settings'],'presentation':presentation(p)})
    lion.show_pet();before=lion.frame;lion.advance();assert lion.frame==before
    lion.next_roam=0;lion.move_tick();assert lion.behavior.state=='roaming'
    before=lion.pos();lion.move_tick();assert lion.pos()!=before
    lion.show_panel();lion.move_tick();assert lion.behavior.state=='idle'
    lion.panel.hide();lion.set_activity('working');lion.next_roam=0;before=lion.pos();lion.move_tick()
    assert lion.pos()==before and lion.behavior.state=='working'
    p['settings'].update(quietMode=True,moodExpressiveness='medium')
    lion.apply_personalization({'settings':p['settings'],'presentation':presentation(p)})
    before=lion.frame;lion.advance();assert lion.frame==before
    lion.action('hide');assert not lion.movement.isActive() and lion.behavior.state=='resting'
    lion.panel.timer.stop();lion.profile_timer.stop();lion.panel.deleteLater();lion.deleteLater();app.processEvents()


def test_missing_or_corrupt_animation_uses_default_portrait(app,tmp_path,monkeypatch):
    monkeypatch.setattr(ui,'PetClient',FakeClient)
    def broken(*args):raise ui.zipfile.BadZipFile('bad animation')
    monkeypatch.setattr(ui.zipfile,'ZipFile',broken)
    lion=ui.Lion(Config(make_home(tmp_path)));settle(app,lambda:lion.panel.metadata_loaded)
    assert len(lion.frames)==1 and not lion.frames[0].isNull() and not lion.mask().isEmpty()
    lion.panel.timer.stop();lion.profile_timer.stop();lion.animation.stop();lion.panel.deleteLater();lion.deleteLater();app.processEvents()
