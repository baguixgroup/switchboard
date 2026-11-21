import subprocess
from pathlib import Path
import pytest
from switchboard.runtime import which


def test_voice_ui_recording_and_conversation_lifecycle():
    node=which('node')
    if not node:pytest.skip('Node is needed only for the frontend lifecycle checks.')
    result=subprocess.run([node,'--test',str(Path(__file__).with_name('voice-ui.test.cjs'))],capture_output=True,text=True,timeout=20)
    assert result.returncode==0,result.stdout+result.stderr
