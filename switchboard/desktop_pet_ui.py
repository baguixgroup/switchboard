"""Laith's native transparent desktop pet and task panel (Qt Widgets, separate process)."""
from __future__ import annotations

import contextlib
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
import json
import os
import queue
import secrets
import threading
import zipfile

from PySide6.QtCore import QObject, QRunnable, QThreadPool, QTimer, Qt, Signal, QLockFile, QRect
from PySide6.QtGui import QAction, QColor, QCursor, QPainter, QPixmap, QRegion, QDesktopServices
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QFormLayout, QHBoxLayout, QInputDialog,
    QLabel, QMenu, QMessageBox, QPlainTextEdit, QPushButton, QTextBrowser, QVBoxLayout, QWidget)

from .desktop_pet import PetClient, atomic_json, endpoint_path, fit_position, ipc, preferences, update_preferences


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


class TaskPanel(QWidget):
    def __init__(self,pet,client,config):
        super().__init__();self.pet=pet;self.client=client;self.config=config;self.jobs=set();self.chat_id=preferences(config)['chat_id']
        self.chat=None;self.busy=False;self.polling=False;self.prepared=None;self.latest_answer='';self.exiting=False;self.latest_task=None;self.connecting=False;self.metadata_loaded=False
        self.setWindowTitle('Laith — tasks and chat');self.setObjectName('LaithTaskPanel');self.setAccessibleName('Laith tasks and chat')
        self.resize(440,650);self.setMinimumSize(360,470)
        self.setStyleSheet('QWidget {font-size:13px;} QPushButton {padding:7px 10px;} QPlainTextEdit,QTextBrowser,QComboBox {padding:6px;}')
        layout=QVBoxLayout(self)
        top=QHBoxLayout();title=QLabel('<b>Laith</b> · Your desktop companion');top.addWidget(title,1)
        reconnect=QPushButton('Reconnect');reconnect.clicked.connect(self.connect_hub);top.addWidget(reconnect)
        new=QPushButton('New chat');new.clicked.connect(self.new_chat);top.addWidget(new);layout.addLayout(top)
        self.connection=QLabel('Connecting to your Switchboard hub…');self.connection.setWordWrap(True);layout.addWidget(self.connection)
        self.transcript=QTextBrowser();self.transcript.setOpenExternalLinks(False);self.transcript.anchorClicked.connect(self.open_link)
        self.transcript.setAccessibleName('Conversation with Laith');layout.addWidget(self.transcript,1)
        form=QFormLayout();self.model=QComboBox();self.model.setAccessibleName('Model');self.ability=QComboBox();self.ability.setAccessibleName('Ability')
        form.addRow('Model',self.model);form.addRow('Ability',self.ability);layout.addLayout(form)
        context=QHBoxLayout();self.tools=QCheckBox('Connected tools');self.tools.setToolTip('Uses your configured Switchboard tools. Installations and credentials keep their separate approvals.')
        self.knowledge=QCheckBox('Project knowledge');context.addWidget(self.tools);context.addWidget(self.knowledge);layout.addLayout(context)
        self.message=QPlainTextEdit();self.message.setPlaceholderText('Ask Laith to help with what you are working on…');self.message.setAccessibleName('Message Laith');self.message.setMaximumHeight(125);layout.addWidget(self.message)
        actions=QHBoxLayout();clipboard=QPushButton('Use copied text');clipboard.setToolTip('Adds a preview of clipboard text to your draft. Nothing is sent until you press Send.');clipboard.clicked.connect(self.use_clipboard)
        self.send=QPushButton('Send');self.send.setDefault(True);self.send.setEnabled(False);self.send.clicked.connect(self.prepare)
        actions.addWidget(clipboard);actions.addStretch();actions.addWidget(self.send);layout.addLayout(actions)
        bottom=QHBoxLayout();self.copy=QPushButton('Copy answer');self.copy.setEnabled(False);self.copy.clicked.connect(self.copy_answer)
        self.goal=QPushButton('Make a team goal');self.goal.setEnabled(False);self.goal.clicked.connect(self.make_goal)
        bottom.addWidget(self.copy);bottom.addWidget(self.goal);layout.addLayout(bottom)
        taskrow=QHBoxLayout();app=QPushButton('Open Switchboard');app.clicked.connect(lambda:self.open_switchboard('#laith'))
        self.review=QPushButton('Review task / approvals');self.review.setEnabled(False);self.review.clicked.connect(lambda:self.open_switchboard('#history/'+self.latest_task) if self.latest_task else None)
        taskrow.addWidget(app);taskrow.addWidget(self.review);layout.addLayout(taskrow)
        note=QLabel('Plans and free models only · Saved memory is included.\nClipboard is read only when you choose Use copied text.');note.setWordWrap(True);layout.addWidget(note)
        self.timer=QTimer(self);self.timer.setInterval(1800);self.timer.timeout.connect(self.poll)
        self.reconnect_timer=QTimer(self);self.reconnect_timer.setInterval(6000);self.reconnect_timer.timeout.connect(self.connect_hub)
        self.connect_hub()

    def connect_hub(self):
        if self.connecting:return
        self.connecting=True
        self.call(self.client.overview,self.loaded,self.connect_failed)

    def connect_failed(self,message):
        self.connecting=False;self.connection.setText('Hub connection unavailable. Retrying automatically… '+message)
        self.reconnect_timer.start()

    def call(self,fn,success,error=None):
        job=Call(fn);self.jobs.add(job)
        def done(value):
            self.jobs.discard(job);success(value)
        def failed(message):
            self.jobs.discard(job)
            if error:error(message)
            else:self.failed(message)
        job.signals.done.connect(done,Qt.ConnectionType.QueuedConnection);job.signals.error.connect(failed,Qt.ConnectionType.QueuedConnection)
        QThreadPool.globalInstance().start(job)

    def failed(self,message):
        self.busy=False;self.polling=False;self.send.setEnabled(True);self.send.setText('Send');self.connection.setText('Could not complete this request: '+message)

    def loaded(self,data):
        self.connecting=False;self.metadata_loaded=True;self.reconnect_timer.stop()
        self.connection.setText('Connected to Switchboard · Requests and results stay in your shared history.')
        selected_model=self.model.currentData() or preferences(self.config)['model'];selected_ability=self.ability.currentData()
        self.model.clear();self.model.addItem('Automatic · plans and free only','')
        for m in data['models']:
            if m.get('available') and 'image_gen' not in m.get('features',[]):self.model.addItem(m['label'],m['key'])
        index=self.model.findData(selected_model);self.model.setCurrentIndex(max(0,index));self.ability.clear()
        for a in data['library']['abilities']:self.ability.addItem(a['name'],a['id'])
        self.ability.setCurrentIndex(max(0,self.ability.findData(selected_ability)))
        self.send.setEnabled(not self.busy)
        active={c['id'] for c in data['library']['chats']}
        if self.chat_id in active:self.call(lambda:self.client.chat(self.chat_id),self.show_chat)
        else:self.chat_id='';self.transcript.setPlainText('Hi, I’m Laith. I can help while you work in other apps. Copy the text you want help with, choose Use copied text, add your instructions, then send.')

    def new_chat(self):
        if self.busy:return
        self.chat_id='';self.chat=None;self.latest_answer='';self.copy.setEnabled(False);self.goal.setEnabled(False);self.transcript.clear();self.message.setFocus()
        update_preferences(self.config,chat_id='')

    def use_clipboard(self):
        value=QApplication.clipboard().text()
        if not value.strip():self.connection.setText('Copy some text in another app, then choose Use copied text.');return
        if len(value)>10000:self.connection.setText('Copied text is too long. Copy a shorter excerpt (up to 10,000 characters).');return
        current=self.message.toPlainText()
        combined=current+('\n\n' if current else '')+'Copied text (reference material):\n'+value
        if len(combined)>12000:self.connection.setText('The draft would exceed 12,000 characters. Shorten it first.');return
        self.message.setPlainText(combined);self.message.setFocus()
        self.connection.setText('Copied text added to your draft. Review it before pressing Send.')

    def copy_answer(self):
        if self.latest_answer:QApplication.clipboard().setText(self.latest_answer);self.connection.setText('Answer copied. Paste it into the app where you need it.')

    def prepare(self):
        message=self.message.toPlainText().strip()
        if self.busy or not message or not self.metadata_loaded:return
        if len(message)>12000:self.connection.setText('Keep the message under 12,000 characters.');return
        self.busy=True;self.send.setEnabled(False);self.send.setText('Preparing…')
        model=self.model.currentData() or '';ability=self.ability.currentData() or 'conversation';tools=self.tools.isChecked();knowledge=self.knowledge.isChecked()
        update_preferences(self.config,model=model)
        def work():
            key=self.chat_id
            if not key:key=self.client.new_chat()['id']
            return key,self.client.prepare(key,message,model,ability,tools,knowledge)
        self.call(work,self.confirm)

    def confirm(self,value):
        key,turn=value;self.chat_id=key;update_preferences(self.config,chat_id=key);self.prepared=turn
        plan=turn['plan']
        if plan.get('needs_confirmation') or turn.get('tools_enabled'):
            box=QMessageBox(self);box.setWindowTitle('Confirm Laith request');box.setTextFormat(Qt.TextFormat.PlainText)
            box.setText('Send to '+plan['model_label']+'?\n\nConnected tools: '+('enabled' if turn.get('tools_enabled') else 'off')+'\nSaved memories: '+str(turn.get('memory_count',0))+'\nKnowledge excerpts: '+str(len(turn.get('sources',[])))+'\n\n'+' '.join(plan.get('warnings',[])))
            box.setStandardButtons(QMessageBox.StandardButton.Ok|QMessageBox.StandardButton.Cancel)
            if box.exec()!=QMessageBox.StandardButton.Ok:self.busy=False;self.send.setEnabled(True);self.send.setText('Send');return
        self.send.setText('Working…');self.connection.setText('Working through '+plan['model_label']+'…')
        self.call(lambda:self.client.send(key,turn['id']),lambda _:self.sent())

    def sent(self):
        self.message.clear();self.timer.start();self.poll()

    def poll(self):
        if not self.chat_id or self.polling:return
        self.polling=True
        self.call(lambda:self.client.chat(self.chat_id),self.show_chat,self.poll_failed)

    def poll_failed(self,message):
        self.polling=False;self.connection.setText('Waiting for the hub. Your request may still finish. '+message)

    def show_chat(self,chat):
        self.polling=False;self.chat=chat;self.chat_id=chat['id']
        turns=[t for t in chat['turns'] if t['status'] not in ('prepared','superseded')]
        text=[];pending=False;self.latest_answer='';self.goal_turn=None
        for turn in turns:
            text+=['YOU\n'+turn['message'],'LAITH · '+(turn.get('plan') or {}).get('model_label','')+'\n'+(turn.get('answer') or 'Working…')]
            if turn.get('error'):text.append('Request status: '+turn['error'])
            if turn['status']=='waiting':text.append('This provider needs an app handoff. Review the linked task in Switchboard and paste the result there.')
            if turn.get('task_id'):text.append('Task: '+turn['task_id'])
            if turn.get('task_id'):self.latest_task=turn['task_id']
            if turn['status'] in ('queued','running','waiting'):pending=True
            if turn['status']=='done':self.latest_answer=turn.get('answer','');self.goal_turn=turn['id']
        self.transcript.setPlainText('\n\n'.join(text));self.transcript.verticalScrollBar().setValue(self.transcript.verticalScrollBar().maximum())
        self.copy.setEnabled(bool(self.latest_answer));self.goal.setEnabled(bool(self.goal_turn) and not pending)
        self.review.setEnabled(bool(self.latest_task))
        self.busy=pending;self.send.setEnabled(not pending);self.send.setText('Working…' if pending else 'Send')
        if pending:self.timer.start()
        else:self.timer.stop();self.connection.setText('Ready. Answers are saved in your Switchboard conversation.')

    def make_goal(self):
        if not self.chat or not self.goal_turn:return
        title,ok=QInputDialog.getText(self,'New team goal','What should the team deliver?')
        if ok and title.strip():
            self.call(lambda:self.client.goal(self.chat_id,self.goal_turn,title.strip()),lambda _:self.connection.setText('Planned goal saved. Open Agent Studio when you are ready to start the team.'))

    def open_link(self,url):
        if url.scheme() in ('https','http'):QDesktopServices.openUrl(url)

    def open_switchboard(self,route):
        from .window import open_app_detached
        open_app_detached(route)

    def closeEvent(self,event):
        if self.exiting:event.accept();return
        self.hide();event.ignore()


class Lion(QWidget):
    def __init__(self,config):
        super().__init__();self.config=config;self.opts=preferences(config);self.frame=0;self.drag=None;self.moved=False
        self.setWindowTitle('Laith — desktop companion');self.setAccessibleName('Laith desktop companion')
        self.setWindowFlags(Qt.WindowType.Tool|Qt.WindowType.FramelessWindowHint|Qt.WindowType.WindowStaysOnTopHint|Qt.WindowType.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground);self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_MacAlwaysShowToolWindow,True)
        self.setToolTip('Laith · Click to talk, drag to move, right-click for options.')
        static=resources.files('switchboard').joinpath('web','static')
        self.frames=[]
        frames=static.joinpath('laith-wave-frames.zip')
        if frames.is_file():
            with zipfile.ZipFile(str(frames)) as archive:
                for name in sorted(archive.namelist()):
                    if name.endswith('.png'):
                        pix=QPixmap();pix.loadFromData(archive.read(name));self.frames.append(pix)
        if not self.frames:self.frames=[QPixmap(str(static.joinpath('laith.png')))]
        self.panel=TaskPanel(self,PetClient(config),config)
        self.animation=QTimer(self);self.animation.setInterval(140);self.animation.timeout.connect(self.advance)
        self.resize_pet(self.opts['size']);self.place()

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
        painter=QPainter(self);pix=self.scaled[self.frame];painter.drawPixmap((self.width()-pix.width())//2,self.height()-pix.height(),pix)

    def advance(self):
        if not self.isVisible() or not self.opts['animation']:return
        self.frame=(self.frame+1)%len(self.scaled);self.refresh_mask();self.update()
        self.animation.setInterval(3500 if self.frame==0 else 140)

    def show_pet(self):
        self.show()
        if self.opts['animation']:self.animation.start()

    def show_panel(self):
        self.show_pet();self.panel.move(*fit_position(self.x()-self.panel.width()+self.width(),self.y()-self.panel.height(),self.panel.width(),self.panel.height(),screens()))
        self.panel.show();self.panel.raise_();self.panel.activateWindow();self.panel.message.setFocus()
        if self.panel.chat_id:self.panel.poll()

    def action(self,action):
        if action=='show':self.show_pet();update_preferences(self.config,enabled=True)
        elif action=='panel':self.show_panel();update_preferences(self.config,enabled=True)
        elif action=='hide':self.hide();self.panel.hide();self.animation.stop();update_preferences(self.config,enabled=False)
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
        animate=menu.addAction('Animate Laith');animate.setCheckable(True);animate.setChecked(self.opts['animation']);animate.triggered.connect(lambda checked:self.action('resume' if checked else 'pause'))
        sizes=menu.addMenu('Size')
        for name,size in [('Small',120),('Medium',180),('Large',240)]:sizes.addAction(name,lambda checked=False,size=size:self.resize_pet(size))
        menu.addSeparator();menu.addAction('Open Switchboard',lambda:self.panel.open_switchboard('#laith'));menu.addAction('Hide Laith',lambda:self.action('hide'))
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
        state.update(visible=lion.isVisible(),panel_visible=lion.panel.isVisible())
    timer=QTimer();timer.setInterval(100);timer.timeout.connect(pump);timer.start()
    lion.show_panel() if panel else lion.show_pet()
    try:return app.exec()
    finally:
        server.shutdown();server.server_close()
        with contextlib.suppress(OSError):endpoint_path(config).unlink()
        lock.unlock()
