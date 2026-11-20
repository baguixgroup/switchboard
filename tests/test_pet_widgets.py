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
