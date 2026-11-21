"""Brand changes keep the existing installation and helper entry points usable."""
import sys
from pathlib import Path
import pytest
from switchboard import paths


@pytest.mark.parametrize('main_name',['Tazrivo','Switchboard'])
def test_laith_helper_opens_new_and_legacy_mac_bundles(tmp_path,monkeypatch,main_name):
    main=tmp_path/(main_name+'.app')/'Contents/MacOS'/main_name
    main.parent.mkdir(parents=True);main.touch()
    helper=main.parents[1]/'Helpers/Laith.app/Contents/MacOS/Laith'
    monkeypatch.setattr(paths,'is_frozen',lambda:True);monkeypatch.setattr(paths,'os_family',lambda:'macos');monkeypatch.setattr(sys,'executable',str(helper))
    assert paths.self_command('launch',gui=True)==[str(main),'launch']


def test_windows_prefers_new_executables_and_retains_legacy_fallback(tmp_path,monkeypatch):
    legacy=tmp_path/'switchboard-cli.exe';legacy.touch()
    gui=tmp_path/'Switchboard.exe';gui.touch()
    monkeypatch.setattr(paths,'is_frozen',lambda:True);monkeypatch.setattr(paths,'os_family',lambda:'windows');monkeypatch.setattr(sys,'executable',str(legacy))
    assert paths.self_command('tray',gui=True)[0]==str(gui)
    current=tmp_path/'Tazrivo.exe';current.touch()
    cli=tmp_path/'tazrivo-cli.exe';cli.touch()
    assert paths.self_command('tray',gui=True)[0]==str(current)
    assert paths.self_command('mcp','serve')[0]==str(cli)


def test_rebrand_keeps_existing_windows_storage(monkeypatch,tmp_path):
    monkeypatch.setattr(paths,'os_family',lambda:'windows');monkeypatch.delenv('SWITCHBOARD_HOME',raising=False);monkeypatch.setenv('APPDATA',str(tmp_path))
    assert paths.config_dir()==tmp_path/'Switchboard'
