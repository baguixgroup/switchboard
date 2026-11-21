"""Laith's native transparent desktop pet and task panel (Qt Widgets, separate process)."""
from __future__ import annotations

import contextlib
import hmac
import html
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
import json
import math
import os
import queue
import re
import secrets
import threading
import zipfile

from PySide6.QtCore import QObject, QRunnable, QThreadPool, QTimer, Qt, Signal, QLockFile, QRect
from PySide6.QtGui import QAction, QColor, QCursor, QPainter, QPixmap, QRegion, QDesktopServices, QTextDocument
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QFormLayout, QHBoxLayout, QInputDialog,
    QLabel, QMenu, QMessageBox, QPlainTextEdit, QPushButton, QTextBrowser, QVBoxLayout, QWidget, QDialog, QFrame, QToolButton)

from .desktop_pet import PetClient, atomic_json, endpoint_path, fit_position, ipc, preferences, update_preferences
from .personalization import CharacterBehavior, Settings, assets


class Signals(QObject):
    done=Signal(object)
    error=Signal(str)


class Call(QRunnable):
    def __init__(self,fn):super().__init__();self.fn=fn;self.signals=Signals()
    def run(self):
        try:self.signals.done.emit(self.fn())
        except Exception as exc:self.signals.error.emit(str(exc)[:1200])


def screens():
    return [(g.x(),g.y(),g.width(),g.height()) for s in QApplication.screens() for g in [s.availableGeometry()]]


def reply_html(answer):
    document=QTextDocument()
    # Model text may contain Markdown, but cannot inject HTML or fetch images.
    answer=re.sub(r'!\[([^\]]*)\]\([^)]*\)',r'\1',answer)
    document.setMarkdown(answer,QTextDocument.MarkdownFeature.MarkdownNoHTML)
    body=document.toHtml().split('<body',1)[1].split('>',1)[1].rsplit('</body>',1)[0]
    return re.sub(r'<img\b[^>]*>','',body)


class Composer(QPlainTextEdit):
    submitted=Signal()
    def __init__(self):
        super().__init__();self.composing=False;self.setFixedHeight(44)
        self.textChanged.connect(lambda:QTimer.singleShot(0,self.adjust_height))
    def adjust_height(self):
        block=self.document().begin();lines=0
        while block.isValid():
            lines+=max(1,block.layout().lineCount());block=block.next()
        height=max(44,min(116,int(lines*self.fontMetrics().lineSpacing()+10)))
        if self.height()!=height:self.setFixedHeight(height)
    def resizeEvent(self,event):
        super().resizeEvent(event);QTimer.singleShot(0,self.adjust_height)
    def inputMethodEvent(self,event):
        self.composing=bool(event.preeditString());super().inputMethodEvent(event)
    def keyPressEvent(self,event):
        if event.key() in (Qt.Key.Key_Return,Qt.Key.Key_Enter) and not event.modifiers()&Qt.KeyboardModifier.ShiftModifier and not self.composing:
            self.submitted.emit();event.accept();return
        super().keyPressEvent(event)


class TaskPanel(QWidget):
    def __init__(self,pet,client,config):
        super().__init__();self.pet=pet;self.client=client;self.config=config;self.jobs=set();self.chat_id=preferences(config)['chat_id']
        self.chat=None;self.busy=False;self.polling=False;self.prepared=None;self.latest_answer='';self.exiting=False;self.latest_task=None;self.connecting=False;self.metadata_loaded=False
        self.waiting_turn=None;self.retry_turn=None;self.goal_turn=None;self.rendered=None;self.send_error=''
        self.setWindowTitle('Laith');self.setObjectName('LaithTaskPanel');self.setAccessibleName('Chat with Laith')
        self.resize(420,540);self.setMinimumSize(350,390)
        self.setStyleSheet('''
            QWidget {font-family:'.AppleSystemUIFont','Segoe UI',sans-serif;font-size:14px;color:#302b27;background:#faf8f5;}
            QLabel#Title {font-size:19px;font-weight:650;} QLabel#Subtitle {font-size:12px;color:#8c8278;}
            QToolButton,QPushButton {border:0;border-radius:10px;padding:7px 10px;background:transparent;}
            QToolButton:hover,QPushButton:hover {background:#eee8e0;}
            QToolButton {font-size:20px;} QPushButton:disabled {color:#b7afa6;}
            QTextBrowser {border:0;background:transparent;font-size:15px;selection-background-color:#e8d5bf;}
            QFrame#Composer {border:1px solid #e0d8cf;border-radius:18px;background:#ffffff;}
            QPlainTextEdit {border:0;background:transparent;font-size:15px;padding:3px;selection-background-color:#e8d5bf;}
            QPushButton#Send {background:#90643f;color:#fff;border-radius:17px;font-size:22px;padding:0;}
            QPushButton#Send:hover {background:#785132;} QPushButton#Send:disabled {background:#e8e1d9;color:#aaa097;}
            QFrame#Notice {background:#f0eae2;border-radius:12px;} QFrame#Notice QLabel {background:transparent;font-size:12px;}
            QDialog {background:#faf8f5;} QComboBox {border:1px solid #ddd5cc;border-radius:8px;padding:8px;background:#fff;min-height:20px;}
            QCheckBox {spacing:8px;padding:6px 0;} QScrollBar:vertical {width:7px;background:transparent;} QScrollBar::handle:vertical {background:#d5cbc0;border-radius:3px;min-height:24px;}
            QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical {height:0;}
        ''')
        layout=QVBoxLayout(self);layout.setContentsMargins(20,16,20,16);layout.setSpacing(12)
        top=QHBoxLayout();top.setSpacing(10)
        avatar=QLabel();self.avatar=avatar;avatar.setFixedSize(38,38);portrait=QPixmap(str(resources.files('switchboard').joinpath('web','static','laith.png')))
        avatar.setPixmap(portrait.scaled(38,38,Qt.AspectRatioMode.KeepAspectRatio,Qt.TransformationMode.SmoothTransformation));top.addWidget(avatar)
        titles=QVBoxLayout();titles.setSpacing(1);title=QLabel('Laith');title.setObjectName('Title');titles.addWidget(title)
        self.subtitle=QLabel('Your companion');self.subtitle.setObjectName('Subtitle');titles.addWidget(self.subtitle);top.addLayout(titles,1)
        self.new=QToolButton();self.new.setText('＋');self.new.setAccessibleName('New chat');self.new.setToolTip('New chat');self.new.clicked.connect(self.new_chat);top.addWidget(self.new)
        talk=QPushButton('Voice');talk.setAccessibleName('Voice chat with Laith');talk.setToolTip('Open voice chat in Tazrivo');talk.clicked.connect(lambda:self.open_switchboard('#laith/voice'));top.addWidget(talk)
        settings=QToolButton();settings.setText('⋯');settings.setAccessibleName('Chat settings');settings.setToolTip('Chat settings');settings.clicked.connect(self.open_settings);top.addWidget(settings);layout.addLayout(top)
        self.transcript=QTextBrowser();self.transcript.setOpenExternalLinks(False);self.transcript.setOpenLinks(False);self.transcript.anchorClicked.connect(self.open_link);self.transcript.setAccessibleName('Conversation with Laith');layout.addWidget(self.transcript,1)
        self.notice=QFrame();self.notice.setObjectName('Notice');notice=QVBoxLayout(self.notice);notice.setContentsMargins(12,10,12,10);notice.setSpacing(5)
        self.connection=QLabel('Connecting…');self.connection.setWordWrap(True);notice.addWidget(self.connection)
        notice_actions=QHBoxLayout();self.recover=QPushButton('Reconnect');self.recover.clicked.connect(self.recover_request);notice_actions.addWidget(self.recover)
        self.review=QPushButton('View activity');self.review.clicked.connect(lambda:self.open_switchboard('#history/'+self.latest_task) if self.latest_task else None);notice_actions.addWidget(self.review);notice_actions.addStretch();notice.addLayout(notice_actions);layout.addWidget(self.notice)
        self.reply_actions=QWidget();reply=QHBoxLayout(self.reply_actions);reply.setContentsMargins(0,0,0,0)
        self.copy=QPushButton('Copy answer');self.copy.setAccessibleName('Copy answer');self.copy.clicked.connect(self.copy_answer);reply.addWidget(self.copy)
        self.goal=QPushButton('Make a goal');self.goal.clicked.connect(self.make_goal);reply.addWidget(self.goal);reply.addStretch();self.reply_actions.hide();layout.addWidget(self.reply_actions)
        composer=QFrame();composer.setObjectName('Composer');box=QVBoxLayout(composer);box.setContentsMargins(12,9,10,9);box.setSpacing(2)
        self.message=Composer();self.message.setPlaceholderText('Message Laith…');self.message.setAccessibleName('Message Laith');self.message.setTabChangesFocus(True);self.message.submitted.connect(self.prepare);box.addWidget(self.message)
        actions=QHBoxLayout();clipboard=QToolButton();clipboard.setText('＋');clipboard.setAccessibleName('Use copied text');clipboard.setToolTip('Add copied text to your draft');clipboard.clicked.connect(self.use_clipboard);actions.addWidget(clipboard)
        hint=QLabel('Enter to send · Shift+Enter for a new line');hint.setStyleSheet('font-size:10px;color:#9b9186;background:transparent;');actions.addWidget(hint,1)
        self.send=QPushButton('↑');self.send.setObjectName('Send');self.send.setAccessibleName('Send');self.send.setToolTip('Send message');self.send.setFixedSize(34,34);self.send.setEnabled(False);self.send.clicked.connect(self.prepare);actions.addWidget(self.send);box.addLayout(actions);layout.addWidget(composer)
        self.settings=QDialog(self);self.settings.setWindowTitle('Chat settings');self.settings.resize(380,400);options=QVBoxLayout(self.settings);options.setContentsMargins(22,20,22,20);options.setSpacing(12)
        form=QFormLayout();self.model=QComboBox();self.model.setAccessibleName('Model');self.ability=QComboBox();self.ability.setAccessibleName('Ability');form.addRow('Reply with',self.model);form.addRow('Writing style',self.ability);options.addLayout(form)
        self.tools=QCheckBox('Use connected tools');self.tools.setToolTip('You confirm tool access before sending. Installations keep their separate approvals.');self.knowledge=QCheckBox('Use project knowledge');options.addWidget(self.tools);options.addWidget(self.knowledge)
        note=QLabel('Uses your plans and free models. Saved memories are included. Copied text is read only when you choose +.');note.setWordWrap(True);note.setStyleSheet('font-size:12px;color:#8c8278;');options.addWidget(note)
        reconnect=QPushButton('Reconnect');reconnect.clicked.connect(self.connect_hub);options.addWidget(reconnect)
        app=QPushButton('Open Tazrivo');app.clicked.connect(lambda:self.open_switchboard('#laith'));options.addWidget(app)
        personalize=QPushButton('Personality & appearance');personalize.clicked.connect(lambda:self.open_switchboard('#laith/personality'));options.addWidget(personalize)
        done=QPushButton('Done');done.clicked.connect(self.settings.accept);options.addWidget(done);options.addStretch()
        self.timer=QTimer(self);self.timer.setInterval(1800);self.timer.timeout.connect(self.poll)
        self.reconnect_timer=QTimer(self);self.reconnect_timer.setInterval(6000);self.reconnect_timer.timeout.connect(self.connect_hub)
        self.connect_hub()

    def open_settings(self):
        self.model.setEnabled(not self.busy);self.ability.setEnabled(not self.busy);self.tools.setEnabled(not self.busy);self.knowledge.setEnabled(not self.busy)
        self.settings.show();self.settings.raise_();self.settings.activateWindow()

    def status(self,message='',action=None):
        self.connection.setText(message);self.notice.setVisible(bool(message))
        self.recover.setVisible(bool(action));self.recover.setText(action or 'Reconnect');self.review.setVisible(bool(self.latest_task))

    def connect_hub(self):
        if self.connecting:return
        self.connecting=True;self.call(self.client.overview,self.loaded,self.connect_failed)

    def connect_failed(self,message):
        self.connecting=False;self.subtitle.setText('Connection unavailable');self.status('Hub connection unavailable. Retrying automatically…','Reconnect');self.reconnect_timer.start()

    def call(self,fn,success,error=None):
        job=Call(fn);self.jobs.add(job)
        def done(value):self.jobs.discard(job);success(value)
        def failed(message):
            self.jobs.discard(job)
            if error:error(message)
            else:self.failed(message)
        job.signals.done.connect(done,Qt.ConnectionType.QueuedConnection);job.signals.error.connect(failed,Qt.ConnectionType.QueuedConnection);QThreadPool.globalInstance().start(job)

    def failed(self,message):
        self.busy=False;self.polling=False;self.new.setEnabled(True);self.send.setEnabled(self.metadata_loaded);self.status(message,'Try again');self.subtitle.setText('Needs a hand')
        self.pet.behavior.cancel()

    def loaded(self,data):
        self.connecting=False;self.metadata_loaded=True;self.reconnect_timer.stop();self.subtitle.setText('Here to help');self.status()
        if data['library'].get('personalization'):self.pet.apply_personalization(data['library']['personalization'])
        selected_model=self.model.currentData() or preferences(self.config)['model'];selected_ability=self.ability.currentData()
        self.model.clear();self.model.addItem('Automatic','')
        for m in data['models']:
            if m.get('available') and not {'image_gen','handoff'}&set(m.get('features',[])):self.model.addItem(m['label'],m['key'])
        self.model.setCurrentIndex(max(0,self.model.findData(selected_model)));self.ability.clear()
        for a in data['library']['abilities']:self.ability.addItem(a['name'],a['id'])
        self.ability.setCurrentIndex(max(0,self.ability.findData(selected_ability)));self.send.setEnabled(not self.busy)
        if self.chat_id in {c['id'] for c in data['library']['chats']}:self.poll()
        else:
            self.chat_id='';self.transcript.setHtml('<h2 style="font-weight:500;color:#52473d;margin-top:65px;">What’s on your mind?</h2><p style="color:#8c8278;">A question, a rough idea, something to write.<br>I’m here to help.</p>')

    def new_chat(self):
        if self.busy and not self.waiting_turn:return
        self.timer.stop();self.chat_id='';self.chat=None;self.latest_answer='';self.goal_turn=None;self.latest_task=None;self.waiting_turn=None;self.retry_turn=None;self.prepared=None;self.busy=False;self.polling=False;self.rendered=None;self.send_error=''
        self.reply_actions.hide();self.status();self.transcript.clear();self.message.clear();self.message.setFocus();self.send.setEnabled(self.metadata_loaded);self.subtitle.setText('A fresh start');update_preferences(self.config,chat_id='')
        self.pet.behavior.cancel()

    def use_clipboard(self):
        value=QApplication.clipboard().text()
        if not value.strip():self.status('Copy some text in another app, then choose +.');return
        if len(value)>10000:self.status('Copy a shorter excerpt (up to 10,000 characters).');return
        current=self.message.toPlainText();combined=current+('\n\n' if current else '')+'Copied text (reference material):\n'+value
        if len(combined)>12000:self.status('The draft is too long. Shorten it first.');return
        self.message.setPlainText(combined);self.message.setFocus();self.status('Copied text added. Review your draft before sending.')

    def copy_answer(self):
        if self.latest_answer:QApplication.clipboard().setText(self.latest_answer);self.subtitle.setText('Answer copied')

    def prepare(self):
        message=self.message.toPlainText().strip()
        if self.busy or not message or not self.metadata_loaded:return
        if len(message)>12000:self.status('Keep the message under 12,000 characters.');return
        self.busy=True;self.send.setEnabled(False);self.status('Connecting to your model…');self.subtitle.setText('Getting ready')
        self.pet.set_activity('working')
        model=self.model.currentData() or '';ability=self.ability.currentData() or 'conversation';tools=self.tools.isChecked();knowledge=self.knowledge.isChecked();key=self.chat_id
        update_preferences(self.config,model=model)
        def work():
            chat_key=key or self.client.new_chat()['id']
            return chat_key,self.client.prepare(chat_key,message,model,ability,tools,knowledge)
        self.call(work,self.confirm)

    def confirm(self,value):
        key,turn=value;self.chat_id=key;update_preferences(self.config,chat_id=key);self.prepared=turn;self.waiting_turn=None
        plan=turn['plan']
        if plan.get('needs_confirmation') or turn.get('tools_enabled') or plan.get('effective_cost_usd',0)>0:
            box=QMessageBox(self);box.setWindowTitle('Confirm request');box.setTextFormat(Qt.TextFormat.PlainText)
            box.setText('Send to '+plan['model_label']+'?\n\nEstimated API use: $'+str(plan.get('effective_cost_usd',0))+'\nConnected tools: '+('enabled' if turn.get('tools_enabled') else 'off')+'\nSaved memories: '+str(turn.get('memory_count',0))+'\nKnowledge excerpts: '+str(len(turn.get('sources',[])))+'\n\n'+' '.join(plan.get('warnings',[])));box.setStandardButtons(QMessageBox.StandardButton.Ok|QMessageBox.StandardButton.Cancel)
            if box.exec()!=QMessageBox.StandardButton.Ok:
                self.busy=False;self.send.setEnabled(True);self.prepared=None;self.pet.behavior.cancel();self.status('Request kept as a draft.');self.poll();return
        self.busy=True;self.send.setEnabled(False);self.subtitle.setText('Thinking');self.status('Sending your message…');self.send_error=''
        self.call(lambda:self.client.send(key,turn['id']),lambda _:self.sent(),self.delivery_unknown)

    def delivery_unknown(self,message):
        self.send_error=message;self.status('Connection interrupted. Checking whether your message was received…','Reconnect');self.timer.start();self.poll()

    def sent(self):
        self.message.clear();self.timer.start();self.poll()

    def poll(self):
        if not self.chat_id or self.polling:return
        key=self.chat_id;self.polling=True
        def received(chat):
            if key!=self.chat_id:return
            self.show_chat(chat)
        def failed(message):
            if key==self.chat_id:self.poll_failed(message)
        self.call(lambda:self.client.chat(key),received,failed)

    def poll_failed(self,message):
        self.polling=False;self.subtitle.setText('Reconnecting');self.status('Connection interrupted. Your request is saved; checking again…','Reconnect')
        self.timer.start()

    def show_chat(self,chat):
        self.polling=False;self.chat=chat;self.chat_id=chat['id']
        retired={t.get('retry_of') for t in chat['turns'] if t['status'] not in ('prepared','superseded')}
        turns=[t for t in chat['turns'] if t['status'] not in ('prepared','superseded','cancelled') and t['id'] not in retired]
        active=next((t for t in reversed(turns) if t['status'] in ('queued','running','waiting')),None)
        self.waiting_turn=active if active and active['status']=='waiting' else None
        self.retry_turn=turns[-1] if not active and turns and turns[-1]['status'] in ('failed','interrupted') else None
        self.latest_answer='';self.goal_turn=None;pieces=[]
        for turn in turns:
            message=html.escape(turn['message']).replace('\n','<br>')
            pieces.append('<table width="100%" cellspacing="0" cellpadding="12"><tr><td width="12%"></td><td bgcolor="#eee6dc"><font color="#42382f">'+message+'</font></td></tr></table><p></p>')
            if turn['status']=='done':
                self.latest_answer=turn.get('answer','');self.goal_turn=turn['id']
                answer=reply_html(turn.get('answer',''));pieces.append('<p><b>Laith</b></p>'+answer+'<br>')
            elif turn['status'] in ('queued','running'):pieces.append('<p style="color:#938575;">Laith is thinking…</p>')
            elif turn['status']=='waiting':pieces.append('<p style="color:#938575;">This request is waiting for an answer from another app.</p>')
            elif turn.get('error'):pieces.append('<p style="color:#9b5b42;">'+html.escape(turn['error'])+'</p>')
            if turn.get('task_id'):self.latest_task=turn['task_id']
            if self.prepared and self.prepared['id']==turn['id']:
                self.prepared=None;self.message.clear();self.send_error=''
        content=''.join(pieces)
        if content!=self.rendered:
            bar=self.transcript.verticalScrollBar();at_end=bar.maximum()-bar.value()<35;before=bar.value()
            self.transcript.setHtml(content);self.rendered=content;bar.setValue(bar.maximum() if at_end else before)
        self.copy.setEnabled(bool(self.latest_answer));self.goal.setEnabled(bool(self.goal_turn) and not active);self.reply_actions.setVisible(bool(self.latest_answer) and not active)
        self.busy=bool(active);self.send.setEnabled(not self.busy and self.metadata_loaded);self.new.setEnabled(not self.busy or bool(self.waiting_turn))
        if active:
            self.pet.set_activity('working')
            self.timer.start()
            if self.waiting_turn:self.subtitle.setText('Waiting for another app');self.status('Your model needs a manual app handoff. Let Laith answer here instead.','Reply here instead')
            else:
                elapsed=max(0,int(time.time()-active['created']));self.subtitle.setText('Thinking · '+str(elapsed)+'s')
                self.status('This is taking longer than usual. You can inspect the request in activity.' if elapsed>90 else '')
        else:
            self.pet.set_activity('reacting' if self.latest_answer else 'idle')
            self.timer.stop();self.subtitle.setText('Here to help')
            if self.retry_turn:self.status('That reply did not finish. Your message is saved.','Try again')
            elif self.send_error:self.status('Your message was not sent. Your draft is still here.','Try again')
            else:self.status()

    def recover_request(self):
        turn=self.waiting_turn or self.retry_turn
        if turn:
            key=self.chat_id;self.recover.setEnabled(False)
            def recovered(value):self.recover.setEnabled(True);self.message.setPlainText(value['message']);self.confirm((key,value))
            def failed(message):self.recover.setEnabled(True);self.status(message,'Try again')
            self.call(lambda:self.client.retry(key,turn['id']),recovered,failed)
        elif self.send_error:self.send_error='';self.prepare()
        elif not self.metadata_loaded:self.connect_hub()
        else:self.connect_hub();self.poll()

    def make_goal(self):
        if not self.chat or not self.goal_turn:return
        title,ok=QInputDialog.getText(self,'New goal','What should the team deliver?')
        if ok and title.strip():self.call(lambda:self.client.goal(self.chat_id,self.goal_turn,title.strip()),lambda _:self.status('Goal saved. Open Tazrivo to start the team.'))

    def open_link(self,url):
        if url.scheme() in ('https','http'):QDesktopServices.openUrl(url)

    def open_switchboard(self,route):
        from .window import open_app_detached
        open_app_detached(route)

    def closeEvent(self,event):
        if self.exiting:event.accept();return
        self.settings.hide();self.hide();event.ignore()

class Lion(QWidget):
    def __init__(self,config):
        super().__init__();self.config=config;self.opts=preferences(config);self.frame=0;self.drag=None;self.moved=False
        self.behavior=CharacterBehavior();self.personal_settings=Settings().model_dump();self.profile_job=False;self.profile_revision=-1
        self.next_roam=time.monotonic()+90;self.roam_until=0;self.roam_direction=1;self.reaction_until=0
        self.setWindowTitle('Laith — desktop companion');self.setAccessibleName('Laith desktop companion')
        self.setWindowFlags(Qt.WindowType.Tool|Qt.WindowType.FramelessWindowHint|Qt.WindowType.WindowStaysOnTopHint|Qt.WindowType.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground);self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_MacAlwaysShowToolWindow,True)
        self.setToolTip('Laith · Click to talk, drag to move, right-click for options.')
        static=resources.files('switchboard').joinpath('web','static')
        self.frames=[]
        frames=static.joinpath('laith-wave-frames.zip')
        if frames.is_file():
            try:
                with zipfile.ZipFile(str(frames)) as archive:
                    for name in sorted(archive.namelist()):
                        if name.endswith('.png'):
                            pix=QPixmap();pix.loadFromData(archive.read(name))
                            if not pix.isNull():self.frames.append(pix)
            except (OSError,zipfile.BadZipFile):self.frames=[]
        if not self.frames:self.frames=[QPixmap(str(static.joinpath('laith.png')))]
        self.default_frames=self.frames[:];self.current_image='/static/laith.png';self.breath_phase=0
        self.panel=TaskPanel(self,PetClient(config),config)
        self.clip=assets()['clips']['wave']
        self.animation=QTimer(self);self.animation.setInterval(self.clip['intervalMs']);self.animation.timeout.connect(self.advance)
        self.movement=QTimer(self);self.movement.setInterval(80);self.movement.timeout.connect(self.move_tick)
        self.profile_timer=QTimer(self);self.profile_timer.setInterval(5000);self.profile_timer.timeout.connect(self.refresh_personalization);self.profile_timer.start()
        self.resize_pet(self.opts['size']);self.place()

    def refresh_personalization(self):
        if self.profile_job or not hasattr(self.panel.client,'personalization'):return
        self.profile_job=True
        def loaded(value):
            self.profile_job=False;self.apply_personalization(value)
        def failed(message):
            self.profile_job=False
            # A disconnected device cannot keep acting on revoked hub consent.
            self.personal_settings=dict(self.personal_settings,quietMode=True)
            self.behavior.configure(self.personal_settings);self.behavior.cancel();self.frame=0;self.refresh_mask();self.update()
        self.panel.call(self.panel.client.personalization,loaded,failed)

    def apply_personalization(self,profile):
        revision=profile.get('revision',0)
        if revision<self.profile_revision:return
        self.profile_revision=revision
        self.personal_settings=profile['settings'];self.behavior.configure(self.personal_settings)
        image=profile['presentation']['image']
        manifest=assets();allowed={manifest['defaultImage']} | {v['image'] for v in manifest['outfits'].values()}
        name=image.removeprefix('/static/')
        if name not in allowed:name=manifest['defaultImage'];image='/static/'+name
        if image!=self.current_image:
            pix=QPixmap(str(resources.files('switchboard').joinpath('web','static',name)))
            if not pix.isNull():
                self.frames=self.default_frames[:] if name==manifest['defaultImage'] else [pix]
                self.frame=0;self.current_image=image;self.resize_pet(self.width())
                self.panel.avatar.setPixmap(pix.scaled(38,38,Qt.AspectRatioMode.KeepAspectRatio,Qt.TransformationMode.SmoothTransformation))
        missing=profile['presentation']['missingAssets'];role=profile['presentation']['activeRole']
        detail=(' · '+role if role else '')+(' · Default artwork (requested assets unavailable)' if missing else '')
        self.setToolTip('Laith'+detail+' · '+self.behavior.state+' · Click to talk, drag to move, right-click for options.')
        self.setAccessibleDescription('Atlas lion. '+self.behavior.state+detail)
        if self.behavior.quiet or self.personal_settings['moodExpressiveness']=='off':self.frame=0;self.refresh_mask();self.update()

    def set_activity(self,state):
        if self.behavior.transition(state,interrupt=True) and state=='reacting':self.reaction_until=time.monotonic()+2
        mood='' if self.personal_settings['moodExpressiveness']=='off' else ' · '+self.behavior.mood
        self.setAccessibleDescription('Laith · '+self.behavior.state+mood)
        self.setToolTip('Laith · '+self.behavior.state+mood+' · Click to talk, drag to move, right-click for options.')

    def move_tick(self):
        now=time.monotonic()
        if self.behavior.state=='reacting' and now>=self.reaction_until:self.behavior.cancel()
        if not self.isVisible() or self.drag is not None or self.panel.isVisible() or self.behavior.quiet or not self.behavior.roaming or not self.opts['animation']:
            if self.behavior.state=='roaming':self.behavior.cancel()
            self.roam_until=0;self.next_roam=now+90;return
        if self.behavior.state=='roaming':
            if now>=self.roam_until:
                self.behavior.cancel();update_preferences(self.config,x=self.x(),y=self.y());return
            point=fit_position(self.x()+self.roam_direction*2,self.y(),self.width(),self.height(),screens())
            self.move(*point)
        elif now>=self.next_roam and self.behavior.transition('roaming'):
            self.roam_direction*=-1;self.roam_until=now+1.5
            self.next_roam=now+(90 if self.personal_settings['roamingActivity']=='low' else 45)

    def resize_pet(self,size):
        self.setFixedSize(size,size);self.scaled=[p.scaled(size,size,Qt.AspectRatioMode.KeepAspectRatio,Qt.TransformationMode.SmoothTransformation) for p in self.frames]
        self.refresh_mask();self.update();update_preferences(self.config,size=size)

    def place(self):
        display=QApplication.primaryScreen().availableGeometry()
        x=self.opts.get('x',display.right()-self.width()-35);y=self.opts.get('y',display.bottom()-self.height()-30)
        self.move(*fit_position(x,y,self.width(),self.height(),screens()))

    def refresh_mask(self):
        pix=self.scaled[self.frame];self.setMask(QRegion(pix.mask()).translated((self.width()-pix.width())//2,self.height()-pix.height()))

    def paintEvent(self,event):
        painter=QPainter(self);pix=self.scaled[self.frame]
        if len(self.frames)==1 and self.opts['animation'] and not self.behavior.quiet and self.personal_settings['moodExpressiveness']!='off':
            scale=.99+.01*math.sin(self.breath_phase)
            painter.translate(self.width()/2,self.height());painter.scale(scale,scale);painter.translate(-self.width()/2,-self.height())
        painter.drawPixmap((self.width()-pix.width())//2,self.height()-pix.height(),pix)

    def advance(self):
        if not self.isVisible() or not self.opts['animation'] or self.behavior.quiet or self.personal_settings['moodExpressiveness']=='off':return
        if len(self.frames)==1:
            self.breath_phase+=.18;self.animation.setInterval(140);self.update();return
        self.frame=(self.frame+1)%len(self.scaled);self.refresh_mask();self.update()
        intensity=2 if self.personal_settings['moodExpressiveness']=='low' else 1
        self.animation.setInterval(self.clip['idlePauseMs']*intensity if self.frame==0 else self.clip['intervalMs'])

    def show_pet(self):
        self.show()
        if self.opts['animation']:self.animation.start()
        self.movement.start()

    def show_panel(self):
        self.show_pet();self.panel.move(*fit_position(self.x()-self.panel.width()+self.width(),self.y()-self.panel.height(),self.panel.width(),self.panel.height(),screens()))
        self.panel.show();self.panel.raise_();self.panel.activateWindow();self.panel.message.setFocus()
        if self.panel.chat_id:self.panel.poll()

    def action(self,action):
        if action=='show':self.show_pet();update_preferences(self.config,enabled=True)
        elif action=='panel':self.show_panel();update_preferences(self.config,enabled=True)
        elif action=='hide':self.hide();self.panel.hide();self.animation.stop();self.movement.stop();self.behavior.cancel();update_preferences(self.config,enabled=False)
        elif action in ('pause','resume'):
            self.opts['animation']=action=='resume';update_preferences(self.config,animation=self.opts['animation'])
            if self.opts['animation']:self.animation.start()
            else:self.animation.stop()
        elif action=='stop':self.panel.exiting=True;QApplication.quit()

    def mousePressEvent(self,event):
        if event.button()==Qt.MouseButton.LeftButton:self.drag=event.globalPosition().toPoint()-self.pos();self.moved=False
        elif event.button()==Qt.MouseButton.RightButton:
            self.open_menu(event.globalPosition().toPoint());event.accept()

    def mouseMoveEvent(self,event):
        if self.drag is not None:
            point=event.globalPosition().toPoint()-self.drag
            if (point-self.pos()).manhattanLength()>3:self.moved=True
            self.move(point)

    def mouseReleaseEvent(self,event):
        if event.button()!=Qt.MouseButton.LeftButton:return
        self.drag=None
        if self.moved:
            self.move(*fit_position(self.x(),self.y(),self.width(),self.height(),screens()));update_preferences(self.config,x=self.x(),y=self.y())
        elif self.panel.isVisible():self.panel.hide()
        else:self.show_panel()

    def contextMenuEvent(self,event):
        # macOS can omit the synthesized context event for a non-activating tool
        # window. Handle mouse presses directly and reserve this path for keys.
        if event.reason()==event.Reason.Keyboard:self.open_menu(event.globalPos())
        event.accept()

    def open_menu(self,position):
        menu=QMenu(self)
        menu.addAction('Talk to Laith',self.show_panel)
        menu.addAction('Personality & appearance',lambda:self.panel.open_switchboard('#laith/personality'))
        animate=menu.addAction('Animate Laith');animate.setCheckable(True);animate.setChecked(self.opts['animation']);animate.triggered.connect(lambda checked:self.action('resume' if checked else 'pause'))
        sizes=menu.addMenu('Size')
        for name,size in [('Small',120),('Medium',180),('Large',240)]:sizes.addAction(name,lambda checked=False,size=size:self.resize_pet(size))
        menu.addSeparator();menu.addAction('Open Tazrivo',lambda:self.panel.open_switchboard('#laith'));menu.addAction('Hide Laith',lambda:self.action('hide'))
        menu.exec(position)


def run_pet(config,panel=False):
    app=QApplication.instance() or QApplication([]);app.setApplicationName('Laith');app.setApplicationDisplayName('Laith');app.setQuitOnLastWindowClosed(False)
    lock=QLockFile(str(config.db_path.parent/'laith-desktop.lock'));lock.setStaleLockTime(0)
    if not lock.tryLock(3000):
        ipc(config,'panel' if panel else 'show');return 0
    lion=Lion(config);token=secrets.token_hex(32);commands=queue.SimpleQueue();state={'visible':True,'panel_visible':panel,'running':True}
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_POST(self):
            if self.path!='/control' or self.headers.get('Origin') or not hmac.compare_digest(self.headers.get('Authorization',''),'Bearer '+token):self.send_error(403);return
            try:
                size=int(self.headers.get('Content-Length','0'))
                if not 0<size<=256:raise ValueError()
                action=json.loads(self.rfile.read(size)).get('action')
                if action not in ('show','hide','panel','pause','resume','stop','status'):raise ValueError()
            except (ValueError,TypeError,AttributeError):self.send_error(400);return
            if action!='status':commands.put(action)
            body=json.dumps(state).encode();self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler);server.daemon_threads=True
    atomic_json(endpoint_path(config),{'port':server.server_port,'token':token,'pid':os.getpid()})
    threading.Thread(target=server.serve_forever,daemon=True).start()
    def pump():
        while not commands.empty():lion.action(commands.get())
        state.update(visible=lion.isVisible(),panel_visible=lion.panel.isVisible(),image=lion.current_image,profile_revision=lion.profile_revision)
    timer=QTimer();timer.setInterval(100);timer.timeout.connect(pump);timer.start()
    lion.show_panel() if panel else lion.show_pet()
    try:return app.exec()
    finally:
        server.shutdown();server.server_close()
        with contextlib.suppress(OSError):endpoint_path(config).unlink()
        lock.unlock()
