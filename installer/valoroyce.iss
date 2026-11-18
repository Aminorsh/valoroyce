; Valoroyce installer. Build with installer\build.ps1, which collects the files
; into installer\stage first. No administrator rights: everything goes under
; %LOCALAPPDATA%\Programs\Valoroyce, models and settings included.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

[Setup]
AppId={{6F1C7B52-3A4E-4E8B-9C2D-5B7A1E0F9D31}
AppName=Valoroyce
AppVersion={#AppVersion}
AppVerName=Valoroyce {#AppVersion}
AppPublisher=Aminorsh
AppPublisherURL=https://github.com/Aminorsh/valoroyce
DefaultDirName={localappdata}\Programs\Valoroyce
DisableProgramGroupPage=yes
DisableDirPage=auto
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\dist
OutputBaseFilename=Valoroyce-Setup-{#AppVersion}
SetupIconFile=stage\icon.ico
UninstallDisplayIcon={app}\icon.ico
UninstallDisplayName=Valoroyce 实时字幕
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes

[Languages]
Name: "chs"; MessagesFile: "ChineseSimplified.isl"

[InstallDelete]
; Replaced as a whole on update, so no file of an older version stays behind.
Type: filesandordirs; Name: "{app}\src"
Type: filesandordirs; Name: "{app}\python"
Type: filesandordirs; Name: "{app}\wheel"

[Files]
Source: "stage\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{userprograms}\Valoroyce"; Filename: "{app}\python\pythonw.exe"; Parameters: "-I ""{app}\start.pyw"""; WorkingDir: "{app}"; IconFilename: "{app}\icon.ico"; Comment: "VALORANT 语音实时字幕"
Name: "{userdesktop}\Valoroyce"; Filename: "{app}\python\pythonw.exe"; Parameters: "-I ""{app}\start.pyw"""; WorkingDir: "{app}"; IconFilename: "{app}\icon.ico"; Comment: "VALORANT 语音实时字幕"; Check: WantShortcut('{userdesktop}', 'desktopicon')
Name: "{userstartup}\Valoroyce"; Filename: "{app}\python\pythonw.exe"; Parameters: "-I ""{app}\start.pyw"""; WorkingDir: "{app}"; IconFilename: "{app}\icon.ico"; Check: WantShortcut('{userstartup}', 'autostart')

[Run]
Filename: "{app}\python\pythonw.exe"; Parameters: "-I ""{app}\start.pyw"""; WorkingDir: "{app}"; Description: "现在启动 Valoroyce"; Flags: postinstall nowait skipifsilent

[UninstallDelete]
; Made by the program, not by this installer.
Type: filesandordirs; Name: "{app}\.venv"
Type: filesandordirs; Name: "{app}\cache"
Type: filesandordirs; Name: "{app}\src"
Type: files; Name: "{app}\valoroyce.log"
Type: files; Name: "{app}\valoroyce.log.1"
Type: files; Name: "{app}\models-to-download.txt"

[Code]
{ Plain check boxes on our own pages, also for the shortcuts: Inno's check
  list box (its task and option pages) redraws its focused item over and
  over on Windows 11, which looks like flashing. }
var
  OptionsPage, ModelPage: TWizardPage;
  DesktopBox, StartupBox: TNewCheckBox;
  OptionsSeen: Boolean;
  ModelBoxes: array[0..4] of TNewCheckBox;

function HasNvidia: Boolean;
begin
  Result := FileExists(ExpandConstant('{sys}\nvidia-smi.exe'));
end;

{ AMD's graphics driver is installed. Also true for AMD graphics built into
  the processor, which is too small for Whisper: SenseVoice stays ticked too,
  and the program uses whichever this PC can run. }
function HasAmd: Boolean;
begin
  Result := FileExists(ExpandConstant('{sys}\atiadlxx.dll'));
end;

{ A copy of the program is there already (with or without models). }
function IsUpdate: Boolean;
begin
  Result := FileExists(AddBackslash(WizardDirValue) + 'start.pyw');
end;

function ShortcutExists(Folder: String): Boolean;
begin
  Result := FileExists(ExpandConstant(Folder) + '\Valoroyce.lnk');
end;

{ The shortcut boxes. A silent install keeps what is there, unless told
  otherwise with /desktopicon=1 or /autostart=0 and so on. }
function WantShortcut(Folder: String; Param: String): Boolean;
var
  Default: String;
begin
  if WizardSilent then
  begin
    Default := '0';
    if ShortcutExists(Folder) then Default := '1';
    Result := ExpandConstant('{param:' + Param + '|' + Default + '}') = '1';
  end
  else if Folder = '{userdesktop}' then
    Result := DesktopBox.Checked
  else
    Result := StartupBox.Checked;
end;

function AddText(Page: TWizardPage; Top: Integer; Indent: Integer; Text: String): TNewStaticText;
begin
  Result := TNewStaticText.Create(Page);
  Result.Parent := Page.Surface;
  Result.Left := Indent;
  Result.Top := Top;
  Result.Width := Page.SurfaceWidth - Indent;
  Result.WordWrap := True;
  Result.Caption := Text;
  Result.AdjustHeight;
end;

function AddBox(Page: TWizardPage; Top: Integer; Title: String; Ticked: Boolean): TNewCheckBox;
begin
  Result := TNewCheckBox.Create(Page);
  Result.Parent := Page.Surface;
  Result.Top := Top;
  Result.Width := Page.SurfaceWidth;
  Result.Height := ScaleY(17);
  Result.Caption := Title;
  Result.Checked := Ticked;
end;

function AddModel(Index: Integer; Top: Integer; Title: String; Text: String; Ticked: Boolean): Integer;
var
  Line: TNewStaticText;
begin
  ModelBoxes[Index] := AddBox(ModelPage, Top, Title, Ticked);
  Line := AddText(ModelPage, Top + ScaleY(20), ScaleX(18), Text);
  Result := Line.Top + Line.Height + ScaleY(12);
end;

procedure InitializeWizard;
var
  Top: Integer;
  Intro: TNewStaticText;
begin
  OptionsPage := CreateCustomPage(wpSelectTasks, '附加选项', '选择要创建的快捷方式。');
  DesktopBox := AddBox(OptionsPage, 0, '在桌面创建快捷方式', True);
  StartupBox := AddBox(OptionsPage, ScaleY(28), '开机时自动启动 Valoroyce', False);

  ModelPage := CreateCustomPage(OptionsPage.ID,
    '选择要下载的模型', '模型在第一次启动时下载，也可以全部不选。');
  Intro := AddText(ModelPage, 0, 0,
    '这里选的模型会在程序第一次打开时自动下载。不选也没关系：程序的「设置与模型」窗口里随时可以下载、关闭或删除任何一个。');
  Top := Intro.Top + Intro.Height + ScaleY(14);
  Top := AddModel(3, Top, 'Whisper large-v3-turbo（约 890 MB）',
    '语音识别，最准确。需要独立显卡：NVIDIA、AMD、Intel 都可以。', HasNvidia or HasAmd);
  Top := AddModel(0, Top, 'Whisper large-v3-turbo，NVIDIA 专用版（约 2.9 GB）',
    '同一个模型换成 faster-whisper 运行，含显卡运行库。一般不需要。', False);
  Top := AddModel(1, Top, 'SenseVoice Small（约 240 MB）',
    '语音识别，只用 CPU，任何电脑都能用。', not HasNvidia);
  Top := AddModel(2, Top, 'Hy-MT2 1.8B（约 1.2 GB）',
    '本地翻译模型。', True);
  Top := AddModel(4, Top, 'X-ASR 中文语音输入（约 140 MB）',
    '说话发消息时识别你说的中文，只用 CPU。', True);
  AddText(ModelPage, Top + ScaleY(4), 0,
    '注意：语音识别目前只能在本机运行，识别模型一个都不选就没有字幕。翻译可以改用你自己的在线服务（需要 API 密钥），那样可以不下载翻译模型。');
end;

procedure CurPageChanged(CurPageID: Integer);
begin
  { Set here, after the folder page: a first install offers the desktop
    shortcut, an update shows what is there. }
  if (CurPageID = OptionsPage.ID) and not OptionsSeen then
  begin
    DesktopBox.Checked := ShortcutExists('{userdesktop}') or not IsUpdate;
    StartupBox.Checked := ShortcutExists('{userstartup}');
    OptionsSeen := True;
  end;
end;

function ShouldSkipPage(PageID: Integer): Boolean;
begin
  { An update keeps the models that are there; the settings window manages them. }
  Result := (PageID = ModelPage.ID) and IsUpdate;
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  Code: Integer;
  Exe: String;
begin
  Result := '';
  Exe := AddBackslash(WizardDirValue) + '.venv\Scripts\valoroyce.exe';
  if FileExists(Exe) then
  begin
    { Close a running copy, so its files can be replaced. }
    Exec(Exe, 'quit', AddBackslash(WizardDirValue), SW_HIDE, ewWaitUntilTerminated, Code);
    Sleep(2000);
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  Chosen: String;
begin
  { Written before the files land, so IsUpdate still sees the old state. }
  if (CurStep = ssInstall) and not IsUpdate then
  begin
    Chosen := '';
    if ModelBoxes[0].Checked then Chosen := Chosen + 'whisper' + #13#10;
    if ModelBoxes[3].Checked then Chosen := Chosen + 'whispercpp' + #13#10;
    if ModelBoxes[1].Checked then Chosen := Chosen + 'sensevoice' + #13#10;
    if ModelBoxes[2].Checked then Chosen := Chosen + 'hy-mt2' + #13#10;
    if ModelBoxes[4].Checked then Chosen := Chosen + 'x-asr' + #13#10;
    ForceDirectories(ExpandConstant('{app}'));
    SaveStringToFile(ExpandConstant('{app}\models-to-download.txt'), Chosen, False);
  end;
  { Unticked by hand on an update: the shortcut that was there goes. }
  if (CurStep = ssPostInstall) and not WizardSilent then
  begin
    if not WantShortcut('{userdesktop}', 'desktopicon') then
      DeleteFile(ExpandConstant('{userdesktop}\Valoroyce.lnk'));
    if not WantShortcut('{userstartup}', 'autostart') then
      DeleteFile(ExpandConstant('{userstartup}\Valoroyce.lnk'));
  end;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  App: String;
  Code: Integer;
begin
  if CurUninstallStep = usUninstall then
  begin
    App := ExpandConstant('{app}');
    if FileExists(App + '\.venv\Scripts\valoroyce.exe') then
    begin
      Exec(App + '\.venv\Scripts\valoroyce.exe', 'quit', App, SW_HIDE, ewWaitUntilTerminated, Code);
      Sleep(2000);
    end;
  end;
  if CurUninstallStep = usPostUninstall then
  begin
    App := ExpandConstant('{app}');
    { Made at run time, with paths that can pass 260 characters: rd with \\?\ copes. }
    Exec(ExpandConstant('{cmd}'), '/c rd /s /q "\\?\' + App + '\.venv"', App, SW_HIDE, ewWaitUntilTerminated, Code);
    Exec(ExpandConstant('{cmd}'), '/c rd /s /q "\\?\' + App + '\python"', App, SW_HIDE, ewWaitUntilTerminated, Code);
    Exec(ExpandConstant('{cmd}'), '/c rd /s /q "\\?\' + App + '\cache"', App, SW_HIDE, ewWaitUntilTerminated, Code);
    RemoveDir(App);  { gone if nothing of the user's is left }
    if DirExists(App + '\models') or FileExists(App + '\config.toml') then
      if MsgBox('同时删除已下载的模型、设置和录音吗？' + #13#10 + #13#10 +
                '选「否」会保留它们，以后重新安装时不用再下载。', mbConfirmation, MB_YESNO) = IDYES then
        DelTree(App, True, True, True);
  end;
end;
