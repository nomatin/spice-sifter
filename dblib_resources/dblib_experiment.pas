{ SPDX-FileCopyrightText: 2026 nomatin }
{ SPDX-License-Identifier: GPL-3.0-only }

function PinHotX(X, Ori, Len : Integer) : Integer;
begin
  Result := X;
  if (Ori mod 4) = 0 then Result := X + Len;
  if (Ori mod 4) = 2 then Result := X - Len;
end;

function PinHotY(Y, Ori, Len : Integer) : Integer;
begin
  Result := Y;
  if (Ori mod 4) = 1 then Result := Y + Len;
  if (Ori mod 4) = 3 then Result := Y - Len;
end;

function CleanValue(V : WideString) : WideString;
begin
  Result := StringReplace(V, #13, ' ', 0);
  Result := StringReplace(Result, #10, ' ', 0);
  Result := StringReplace(Result, #9, ' ', 0);
end;

procedure AddError(S : TStringList; Stage, Code, Msg : WideString);
begin
  S.Add('stage_' + Stage + '=FAIL');
  S.Add('error_stage=' + Stage);
  S.Add('error_code=' + Code);
  S.Add('error_message=' + CleanValue(Msg));
end;

function CountComponents(Doc : ISch_Document) : Integer;
var It : ISch_Iterator; O : Variant;
begin
  Result := 0;
  It := Doc.SchIterator_Create;
  It.AddFilter_ObjectSet(MkSet(eSchComponent));
  try
    O := It.FirstSchObject;
    while O <> Nil do begin Inc(Result); O := It.NextSchObject; end;
  finally Doc.SchIterator_Destroy(It); end;
end;

function CountWires(Doc : ISch_Document) : Integer;
var It : ISch_Iterator; O : Variant;
begin
  Result := 0;
  It := Doc.SchIterator_Create;
  It.AddFilter_ObjectSet(MkSet(eWire));
  try
    O := It.FirstSchObject;
    while O <> Nil do begin Inc(Result); O := It.NextSchObject; end;
  finally Doc.SchIterator_Destroy(It); end;
end;

function CountNetLabels(Doc : ISch_Document) : Integer;
var It : ISch_Iterator; O : Variant;
begin
  Result := 0;
  It := Doc.SchIterator_Create;
  It.AddFilter_ObjectSet(MkSet(eNetLabel));
  It.SetState_IterationDepth(eIterateFirstLevel);
  try
    O := It.FirstSchObject;
    while O <> Nil do begin Inc(Result); O := It.NextSchObject; end;
  finally Doc.SchIterator_Destroy(It); end;
end;

function FindComponentByUniqueId(Doc : ISch_Document; Uid : WideString) : ISch_Component;
var It : ISch_Iterator; O : Variant;
begin
  Result := Nil;
  It := Doc.SchIterator_Create;
  It.AddFilter_ObjectSet(MkSet(eSchComponent));
  try
    O := It.FirstSchObject;
    while O <> Nil do begin
      if CompareText(O.UniqueId, Uid) = 0 then begin Result := O; O := Nil; end
      else O := It.NextSchObject;
    end;
  finally Doc.SchIterator_Destroy(It); end;
end;

function FindFixture(Doc : ISch_Document) : ISch_Component;
var It : ISch_Iterator; O : Variant;
begin
  Result := Nil;
  It := Doc.SchIterator_Create;
  It.AddFilter_ObjectSet(MkSet(eSchComponent));
  try
    O := It.FirstSchObject;
    while O <> Nil do begin
      if (O.LibReference = 'DC Current Source') and (O.Designator.Text = 'I1') then begin
        Result := O; O := Nil;
      end else O := It.NextSchObject;
    end;
  finally Doc.SchIterator_Destroy(It); end;
end;

function SetFixtureCurrent(Doc : ISch_Document; Value : WideString) : Boolean;
var It, Child : ISch_Iterator; O, P : Variant; C : ISch_Component; Found : Boolean;
begin
  Result := False;
  It := Doc.SchIterator_Create;
  It.AddFilter_ObjectSet(MkSet(eSchComponent));
  try
    O := It.FirstSchObject;
    while O <> Nil do begin
      C := O;
      if (C.LibReference = 'DC Current Source') and (C.Designator.Text = 'I1') then begin
        Found := False;
        Child := C.SchIterator_Create;
        Child.AddFilter_ObjectSet(MkSet(eParameter));
        try
          P := Child.FirstSchObject;
          while P <> Nil do begin
            if P.GetState_Name = 'DC' then begin
              SchServer.ProcessControl.PreProcess(Doc, '');
              try
                SchServer.RobotManager.SendMessage(P.I_ObjectAddress, c_BroadCast, SCHM_BeginModify, c_NoEventData);
                P.SetState_Text(Value);
                SchServer.RobotManager.SendMessage(P.I_ObjectAddress, c_BroadCast, SCHM_EndModify, c_NoEventData);
              finally SchServer.ProcessControl.PostProcess(Doc, ''); end;
              Found := True;
            end;
            P := Child.NextSchObject;
          end;
        finally C.SchIterator_Destroy(Child); end;
        Result := Found;
        O := Nil;
      end else O := It.NextSchObject;
    end;
  finally Doc.SchIterator_Destroy(It); end;
end;

procedure AddWire(Doc : ISch_Document; X1, Y1, X2, Y2 : Integer; Journal : TStringList);
var W : Variant;
begin
  W := SchServer.SchObjectFactory(eWire, eCreate_Default);
  W.SetState_VerticesCount(2);
  W.SetState_Vertex(1, Point(MilsToCoord(X1), MilsToCoord(Y1)));
  W.SetState_Vertex(2, Point(MilsToCoord(X2), MilsToCoord(Y2)));
  SchServer.ProcessControl.PreProcess(Doc, '');
  try
    Doc.RegisterSchObjectInContainer(W);
    SchServer.RobotManager.SendMessage(Doc.I_ObjectAddress, c_BroadCast,
      SCHM_PrimitiveRegistration, W.I_ObjectAddress);
  finally SchServer.ProcessControl.PostProcess(Doc, ''); end;
  Journal.Add('WIRE=' + W.GetState_UniqueId);
  W := Nil;
end;

procedure AddNetLabel(Doc : ISch_Document; X, Y : Integer; T : WideString; Journal : TStringList);
var L : Variant;
begin
  L := SchServer.SchObjectFactory(eNetLabel, eCreate_Default);
  L.SetState_Location(Point(MilsToCoord(X), MilsToCoord(Y)));
  L.SetState_Text(T);
  SchServer.ProcessControl.PreProcess(Doc, '');
  try
    Doc.RegisterSchObjectInContainer(L);
    SchServer.RobotManager.SendMessage(Doc.I_ObjectAddress, c_BroadCast,
      SCHM_PrimitiveRegistration, L.I_ObjectAddress);
  finally SchServer.ProcessControl.PostProcess(Doc, ''); end;
  Journal.Add('LABEL=' + L.GetState_UniqueId);
  L := Nil;
end;

function DeleteByUniqueId(Doc : ISch_Document; Uid : WideString) : Boolean;
var It : ISch_Iterator; O : Variant; Found : Boolean;
begin
  Result := False;
  Found := False;
  It := Doc.SchIterator_Create;
  It.SetState_IterationDepth(eIterateFirstLevel);
  try
    O := It.FirstSchObject;
    while O <> Nil do begin
      if CompareText(O.GetState_UniqueId, Uid) = 0 then begin
        SchServer.ProcessControl.PreProcess(Doc, '');
        try
          Doc.RemoveSchObject(O);
          SchServer.RobotManager.SendMessage(Doc.I_ObjectAddress, c_BroadCast,
            SCHM_PrimitiveRegistration, O.I_ObjectAddress);
        finally SchServer.ProcessControl.PostProcess(Doc, ''); end;
        Found := True;
        O := Nil;
      end else O := It.NextSchObject;
    end;
  finally Doc.SchIterator_Destroy(It); end;
  Result := Found;
end;

procedure DeleteComponentExact(Doc : ISch_Document; Uid : WideString);
var C : ISch_Component;
begin
  C := FindComponentByUniqueId(Doc, Uid);
  if C <> Nil then begin
    SchServer.ProcessControl.PreProcess(Doc, '');
    try
      Doc.RemoveSchObject(C);
      SchServer.RobotManager.SendMessage(Doc.I_ObjectAddress, c_BroadCast,
        SCHM_PrimitiveRegistration, C.I_ObjectAddress);
    finally SchServer.ProcessControl.PostProcess(Doc, ''); end;
  end;
end;

{ Remove only objects recorded in this run's journal. No coordinate fallback. }
procedure CleanupCreatedObjects(Doc : ISch_Document; DutUid : WideString;
  Journal : TStringList; S : TStringList);
var I, Expected, Removed : Integer; Uid : WideString; Ok : Boolean;
begin
  Expected := Journal.Count;
  if DutUid <> '' then Inc(Expected);
  Removed := 0;
  if DutUid <> '' then begin
    if FindComponentByUniqueId(Doc, DutUid) <> Nil then begin
      DeleteComponentExact(Doc, DutUid);
      Inc(Removed);
    end;
  end;
  for I := 0 to Journal.Count - 1 do begin
    if Pos('WIRE=', Journal[I]) = 1 then begin
      if DeleteByUniqueId(Doc, Copy(Journal[I], 6, 99)) then Inc(Removed);
    end;
    if Pos('LABEL=', Journal[I]) = 1 then begin
      if DeleteByUniqueId(Doc, Copy(Journal[I], 7, 99)) then Inc(Removed);
    end;
  end;
  Ok := True;
  if (DutUid <> '') and (FindComponentByUniqueId(Doc, DutUid) <> Nil) then Ok := False;
  if Removed < Expected then Ok := False;
  if FindFixture(Doc) = Nil then Ok := False;
  S.Add('cleanup_expected=' + IntToStr(Expected));
  S.Add('cleanup_removed=' + IntToStr(Removed));
  S.Add('components_final=' + IntToStr(CountComponents(Doc)));
  S.Add('wires_final=' + IntToStr(CountWires(Doc)));
  S.Add('netlabels_final=' + IntToStr(CountNetLabels(Doc)));
  if Ok then
    S.Add('stage_schematic_cleanup=PASS')
  else begin
    S.Add('stage_schematic_cleanup=FAIL');
    S.Add('cleanup_error=SCHEMATIC_CLEANUP_FAILED');
  end;
end;

procedure RunExperiment;
var
  S, Journal : TStringList;
  D : IDocument;
  Doc : ISch_Document;
  Comp : ISch_Component;
  It, Child : ISch_Iterator;
  O, P, M, Pin : Variant;
  LibPath, LibName, TableName, TargetId, ProjPath, DocPath,
  NsxPath, SdfPath, RunDir, DcValue : WideString;
  Before, After, I, J, PinCount : Integer;
  Pin1X, Pin1Y, Pin2X, Pin2Y, ModelIndex : Integer;
  Pin1Desig, Pin2Desig, DutUid, SimModelName, SimPortMap : WideString;
  Workspace : IWorkspace;
  ProjectObj : IProject;
  LogicalDoc : IDocument;
  Net : INet;
  NetPin : INetItem;
  FoundDutP, FoundGnd, SimModelFound : Boolean;
  CompileResult, Failed, IdMatch, LibraryMatch, TableMatch : Boolean;
  Cnt : Integer;
  Tmp : WideString;
begin
  RunDir := '__RUN_DIR__';
  ProjPath := '__PROJ_PATH__';
  DocPath := '__DOC_PATH__';
  NsxPath := '__NSX_PATH__';
  SdfPath := '__SDF_PATH__';
  LibPath := '__LIB_PATH__';
  LibName := '__LIB_NAME__';
  TableName := '__TABLE_NAME__';
  TargetId := '__TARGET_ID__';
  DcValue := '__DC_VALUE__';

  S := TStringList.Create;
  Journal := TStringList.Create;
  Failed := False;
  DutUid := '';
  SimModelFound := False;
  SimModelName := '';
  SimPortMap := '';
  PinCount := 0;
  Pin1Desig := '';
  Pin2Desig := '';
  Doc := Nil;
  D := Nil;

  S.Add('stage_library_lookup=NOT_RUN');
  S.Add('stage_component_placement=NOT_RUN');
  S.Add('stage_simulation_model=NOT_RUN');
  S.Add('stage_connectivity=NOT_RUN');
  S.Add('stage_netlist_generation=NOT_RUN');
  S.Add('stage_simulation=NOT_RUN');
  S.Add('stage_schematic_cleanup=NOT_RUN');

  try
    S.Add('STARTED');
    S.SaveToFile(RunDir + '\started.txt');
    S.Add('run_dir=' + RunDir);
    S.Add('library_path=' + LibPath);
    S.Add('library_name=' + LibName);
    S.Add('table_name=' + TableName);
    S.Add('database_keys=' + TargetId);
    S.Add('fixture_current_a=' + DcValue);

    ResetParameters;
    AddStringParameter('ObjectKind', 'Project');
    AddStringParameter('FileName', ProjPath);
    RunProcess('WorkspaceManager:OpenObject');
    ResetParameters;
    AddStringParameter('ObjectKind', 'Document');
    AddStringParameter('FileName', DocPath);
    RunProcess('WorkspaceManager:OpenObject');
    D := Client.GetDocumentByPath(DocPath);
    Doc := SchServer.GetSchDocumentByPath(DocPath);
    if (D = Nil) or (Doc = Nil) then begin
      AddError(S, 'library_lookup', 'SCHEMATIC_NOT_OPENED',
        'Test project or schematic could not be opened');
      Failed := True;
    end;

    if not Failed then begin
      Client.ShowDocument(D);
      Doc := SchServer.GetSchDocumentByPath(DocPath);
      if FindFixture(Doc) = Nil then begin
        AddError(S, 'component_placement', 'FIXTURE_NOT_FOUND',
          'Prepared fixture current source I1 is absent');
        Failed := True;
      end;
    end;

    if not Failed then begin
      if not SetFixtureCurrent(Doc, DcValue) then begin
        AddError(S, 'component_placement', 'FIXTURE_CURRENT_FAILED',
          'Could not set DC current of fixture I1');
        Failed := True;
      end;
    end;

    if not Failed then begin
      Before := CountComponents(Doc);
      S.Add('components_before=' + IntToStr(Before));
      Comp := SchServer.LoadComponentFromDatabaseLibrary(LibPath, TableName, TargetId);
      if Comp = Nil then begin
        S.Add('stage_library_lookup=FAIL');
        AddError(S, 'library_lookup', 'COMPONENT_ID_NOT_FOUND',
          'DbLib loader returned nil for page/id');
        Failed := True;
      end else begin
        S.Add('stage_library_lookup=PASS');
        Comp.MoveToXY(MilsToCoord(5000), MilsToCoord(3000));
        Comp.SetState_Orientation(0);
        SchServer.ProcessControl.PreProcess(Doc, '');
        try
          Doc.RegisterSchObjectInContainer(Comp);
          SchServer.RobotManager.SendMessage(Doc.I_ObjectAddress, c_BroadCast,
            SCHM_PrimitiveRegistration, Comp.I_ObjectAddress);
        finally SchServer.ProcessControl.PostProcess(Doc, ''); end;
        Doc.GraphicallyInvalidate;
        DutUid := Comp.UniqueId;
        After := CountComponents(Doc);
        S.Add('components_after=' + IntToStr(After));
        if After <> Before + 1 then begin
          AddError(S, 'component_placement', 'PLACEMENT_FAILED',
            'Component count did not increase by one');
          Failed := True;
        end else
          S.Add('stage_component_placement=PASS');
      end;
    end;

    if not Failed then begin
      S.Add('unique_id=' + DutUid);
      S.Add('lib_reference=' + Comp.LibReference);
      S.Add('database_library_name=' + Comp.DatabaseLibraryName);
      S.Add('database_table_name=' + Comp.DatabaseTableName);
      S.Add('database_keys=' + Comp.GetState_DatabaseLibraryKeys('[[/]]'));
      S.Add('design_item_id=' + Comp.DesignItemId);
      S.Add('x_mil=' + IntToStr(CoordToMils(Comp.Location.X)));
      S.Add('y_mil=' + IntToStr(CoordToMils(Comp.Location.Y)));

      Child := Comp.SchIterator_Create;
      Child.AddFilter_ObjectSet(MkSet(eParameter));
      try
        P := Child.FirstSchObject;
        while P <> Nil do begin
          Tmp := P.GetState_Name;
          S.Add('parameter.' + Tmp + '=' + CleanValue(P.GetState_Text));
          P := Child.NextSchObject;
        end;
      finally Comp.SchIterator_Destroy(Child); end;

      Child := Comp.SchIterator_Create;
      Child.AddFilter_ObjectSet(MkSet(eImplementation));
      ModelIndex := 0;
      try
        M := Child.FirstSchObject;
        while M <> Nil do begin
          if M.GetState_ModelType = 'SIM' then begin
            if (not SimModelFound) or M.GetState_IsCurrent then begin
              SimModelFound := True;
              SimModelName := M.GetState_ModelName;
              SimPortMap := CleanValue(M.GetState_MapAsString);
            end;
          end;
          S.Add('implementation.' + IntToStr(ModelIndex) + '=' +
            M.GetState_ModelName + '|' + M.GetState_ModelType +
            '|current=' + IntToStr(Ord(M.GetState_IsCurrent)) +
            '|database=' + IntToStr(Ord(M.GetState_DatabaseModel)) +
            '|files=' + IntToStr(M.GetState_DatafileLinkCount));
          S.Add('implementation.map.' + IntToStr(ModelIndex) + '=' +
            CleanValue(M.GetState_MapAsString));
          S.Add('implementation.description.' + IntToStr(ModelIndex) + '=' +
            CleanValue(M.GetState_Description));
          Inc(ModelIndex);
          M := Child.NextSchObject;
        end;
      finally Comp.SchIterator_Destroy(Child); end;

      S.Add('sim_model_found=' + BoolToStr(SimModelFound, True));
      S.Add('sim_model_name=' + SimModelName);
      S.Add('sim_port_map=' + SimPortMap);

      Child := Comp.SchIterator_Create;
      Child.AddFilter_ObjectSet(MkSet(ePin));
      Child.SetState_IterationDepth(eIterateFirstLevel);
      try
        Pin := Child.FirstSchObject;
        while Pin <> Nil do begin
          Inc(PinCount);
          S.Add('pin.' + IntToStr(PinCount) + '.name=' + Pin.GetState_Name);
          S.Add('pin.' + IntToStr(PinCount) + '.designator=' + Pin.GetState_Designator);
          S.Add('pin.' + IntToStr(PinCount) + '.x=' +
            IntToStr(CoordToMils(Pin.Location.X)));
          S.Add('pin.' + IntToStr(PinCount) + '.y=' +
            IntToStr(CoordToMils(Pin.Location.Y)));
          if PinCount = 1 then begin
            Pin1X := PinHotX(CoordToMils(Pin.Location.X), Ord(Pin.GetState_Orientation),
              CoordToMils(Pin.GetState_PinLength));
            Pin1Y := PinHotY(CoordToMils(Pin.Location.Y), Ord(Pin.GetState_Orientation),
              CoordToMils(Pin.GetState_PinLength));
            Pin1Desig := Pin.GetState_Designator;
            S.Add('pin.1.hotspot=' + IntToStr(Pin1X) + '|' + IntToStr(Pin1Y));
          end;
          if PinCount = 2 then begin
            Pin2X := PinHotX(CoordToMils(Pin.Location.X), Ord(Pin.GetState_Orientation),
              CoordToMils(Pin.GetState_PinLength));
            Pin2Y := PinHotY(CoordToMils(Pin.Location.Y), Ord(Pin.GetState_Orientation),
              CoordToMils(Pin.GetState_PinLength));
            Pin2Desig := Pin.GetState_Designator;
            S.Add('pin.2.hotspot=' + IntToStr(Pin2X) + '|' + IntToStr(Pin2Y));
          end;
          Pin := Child.NextSchObject;
        end;
      finally Comp.SchIterator_Destroy(Child); end;
      S.Add('pin_count=' + IntToStr(PinCount));
      S.Add('pin.1.designator=' + Pin1Desig);
      S.Add('pin.2.designator=' + Pin2Desig);

      IdMatch := CompareText(Comp.GetState_DatabaseLibraryKeys('[[/]]'), TargetId) = 0;
      S.Add('id_match=' + BoolToStr(IdMatch, True));
      LibraryMatch := (CompareText(Comp.DatabaseLibraryName, LibName) = 0) or
        (Pos(UpperCase(LibName), UpperCase(Comp.DatabaseLibraryName)) > 0);
      S.Add('library_match=' + BoolToStr(LibraryMatch, True));
      TableMatch := CompareText(Comp.DatabaseTableName, TableName) = 0;
      S.Add('table_match=' + BoolToStr(TableMatch, True));

      if not IdMatch then begin
        AddError(S, 'library_lookup', 'COMPONENT_ID_MISMATCH',
          'Loaded component ID does not match requested ID');
        Failed := True;
      end;
      if (not Failed) and (not LibraryMatch) then begin
        AddError(S, 'library_lookup', 'LIBRARY_SOURCE_MISMATCH',
          'Loaded component does not come from the requested DbLib');
        Failed := True;
      end;
      if (not Failed) and (not TableMatch) then begin
        AddError(S, 'library_lookup', 'TABLE_NAME_MISMATCH',
          'Loaded component table does not match requested page');
        Failed := True;
      end;
      if not Failed then begin
        if not SimModelFound then begin
          AddError(S, 'simulation_model', 'MISSING_SIMULATION_MODEL',
            'Component has no SIM implementation');
          Failed := True;
        end else if SimPortMap = '' then begin
          AddError(S, 'simulation_model', 'SIM_PORT_MAP_MISSING',
            'SIM implementation has no port map');
          Failed := True;
        end else
          S.Add('stage_simulation_model=PASS');
      end;
      if (not Failed) and (PinCount <> 2) then begin
        AddError(S, 'component_placement', 'PIN_COUNT_UNSUPPORTED',
          'This fixture supports exactly two pins');
        Failed := True;
      end;
    end;

    if not Failed then begin
      AddWire(Doc, Pin1X, Pin1Y, Pin1X - 200, Pin1Y, Journal);
      AddNetLabel(Doc, Pin1X - 200, Pin1Y, 'DUT_P', Journal);
      AddWire(Doc, Pin2X, Pin2Y, Pin2X + 200, Pin2Y, Journal);
      AddNetLabel(Doc, Pin2X + 200, Pin2Y, 'GND', Journal);

      Workspace := GetWorkspace;
      ProjectObj := Workspace.DM_GetProjectFromPath(ProjPath);
      if ProjectObj = Nil then begin
        AddError(S, 'connectivity', 'PROJECT_NOT_FOUND',
          'Project object is not available for compilation');
        Failed := True;
      end else begin
        CompileResult := ProjectObj.DM_Compile;
        S.Add('compile_result=' + BoolToStr(CompileResult, True));
        S.Add('violation_count=' + IntToStr(ProjectObj.DM_ViolationCount));
        FoundDutP := False;
        FoundGnd := False;
        for I := 0 to ProjectObj.DM_LogicalDocumentCount - 1 do begin
          LogicalDoc := ProjectObj.DM_LogicalDocuments(I);
          if CompareText(LogicalDoc.DM_FullPath, DocPath) = 0 then begin
            S.Add('net_count=' + IntToStr(LogicalDoc.DM_NetCount));
            for J := 0 to LogicalDoc.DM_NetCount - 1 do begin
              Net := LogicalDoc.DM_Nets(J);
              if Net <> Nil then begin
                S.Add('net.' + IntToStr(J) + '.name=' + Net.DM_NetName +
                  '|pins=' + IntToStr(Net.DM_PinCount));
                for Cnt := 0 to Net.DM_PinCount - 1 do begin
                  NetPin := Net.DM_Pins(Cnt);
                  if NetPin <> Nil then begin
                    S.Add('net.' + IntToStr(J) + '.pin=' +
                      NetPin.DM_LogicalPartDesignator + '|' +
                      NetPin.DM_PinNumber + '|uid=' + NetPin.DM_PartUniqueId);
                    if (Net.DM_NetName = 'DUT_P') and
                       (Pos(DutUid, NetPin.DM_PartUniqueId) > 0) and
                       (CompareText(NetPin.DM_PinNumber, Pin1Desig) = 0) then
                      FoundDutP := True;
                    if (Net.DM_NetName = 'GND') and
                       (Pos(DutUid, NetPin.DM_PartUniqueId) > 0) and
                       (CompareText(NetPin.DM_PinNumber, Pin2Desig) = 0) then
                      FoundGnd := True;
                  end;
                end;
              end;
            end;
          end;
        end;
        if FoundDutP and FoundGnd then
          S.Add('stage_connectivity=PASS')
        else begin
          AddError(S, 'connectivity', 'CONNECTIVITY_FAILED',
            'Compiled nets do not contain expected DUT pins');
          Failed := True;
        end;
      end;
    end;

    if not Failed then begin
      if D.DoFileSave('') then S.Add('schematic_saved_before_sim=1');
      Client.ShowDocument(D);
      Doc := SchServer.GetSchDocumentByPath(DocPath);
      RunProcess('ADVSIM:RunSimulator');
      if FileExists(NsxPath) then begin
        S.Add('stage_netlist_generation=PASS');
        S.Add('nsx_path=' + NsxPath);
      end else begin
        AddError(S, 'netlist_generation', 'NSX_NOT_GENERATED',
          'Altium did not generate the expected NSX');
        Failed := True;
      end;
    end;

    if not Failed then begin
      ResetParameters;
      AddStringParameter('ObjectKind', 'Document');
      AddStringParameter('FileName', NsxPath);
      RunProcess('WorkspaceManager:OpenObject');
      D := Client.GetDocumentByPath(NsxPath);
      if D = Nil then begin
        AddError(S, 'simulation', 'NSX_NOT_OPENED',
          'Altium-generated NSX could not be opened');
        Failed := True;
      end else begin
        Client.ShowDocument(D);
        RunProcess('ADVSIM:RunSimulator');
        if Client.CurrentView <> Nil then begin
          S.Add('current_after_sim=' + Client.CurrentView.OwnerDocument.FileName);
          Client.CurrentView.OwnerDocument.DoFileSave('');
        end;
        if FileExists(SdfPath) then begin
          S.Add('stage_simulation=PASS');
          S.Add('sdf_path=' + SdfPath);
        end else begin
          AddError(S, 'simulation', 'SDF_NOT_SAVED',
            'MixedSim did not produce the expected SDF');
          Failed := True;
        end;
      end;
    end;
  except
    AddError(S, 'altium', 'ALTIUM_SCRIPT_EXCEPTION',
      'Unhandled exception in DbLib experiment script');
    Failed := True;
  end;

  { Cleanup is mandatory after placement, including FAIL/ERROR paths. }
  try
    if D <> Nil then begin
      D := Client.GetDocumentByPath(DocPath);
      if D <> Nil then Client.ShowDocument(D);
      Doc := SchServer.GetSchDocumentByPath(DocPath);
    end;
    if Doc = Nil then begin
      S.Add('stage_schematic_cleanup=FAIL');
      S.Add('cleanup_error=SCHEMATIC_CLEANUP_FAILED');
    end else begin
      CleanupCreatedObjects(Doc, DutUid, Journal, S);
      if D <> Nil then begin
        if D.DoFileSave('') then S.Add('schematic_saved_after_cleanup=1')
        else S.Add('schematic_saved_after_cleanup=0');
      end;
    end;
  except
    S.Add('stage_schematic_cleanup=FAIL');
    S.Add('cleanup_error=SCHEMATIC_CLEANUP_FAILED');
  end;

  if Failed then S.Add('script_failed=1') else S.Add('script_failed=0');
  S.Add('STATUS=OK');
  S.SaveToFile(RunDir + '\status.txt');
  S.Free;
  Journal.Free;
end;
