import sys
import pytest


@pytest.mark.skipif(sys.platform!='darwin',reason='Requires the real Cocoa protocol bridge')
def test_native_reopen_protocol_forwards_to_window_and_retains_new_callback():
    pytest.importorskip('AppKit')
    from switchboard.tray import macos_reopen_delegate
    class Application:
        def setDelegate_(self,delegate):self.delegate=delegate
    app=Application();called=[]
    first=macos_reopen_delegate(app,lambda:called.append('first'))
    assert app.delegate is first
    assert first.applicationShouldHandleReopen_hasVisibleWindows_(None,False) is False
    second=macos_reopen_delegate(app,lambda:called.append('second'))
    assert second is app.delegate
    assert second.applicationShouldHandleReopen_hasVisibleWindows_(None,True) is False
    assert called==['first','second']
