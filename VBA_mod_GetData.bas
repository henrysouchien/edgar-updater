Attribute VB_Name = "mod_GetData"
' =======================================================================================
' MODULE:      EDGAR_Integration_Macros
' AUTHOR:      Henry Chien
' CONTACT:     support@henrychien.com
' LAST UPDATED: 2024-05-15
'
' DESCRIPTION:
'     Automates the retrieval and import of updated financial data from the SEC EDGAR pipeline.
'     This module pulls filings via a hosted Flask app, waits for download completion, opens
'     the resulting Excel file, and copies the relevant data into the Raw_data sheet for use
'     in model update macros (e.g., UpdateModel).
'
' WORKFLOW OVERVIEW:
'     1. Run_Edgar_Update
'         -> Calls SetupRanges
'         -> Triggers external download via Get_EDGAR_Data
'         -> Waits for downloaded file (WaitFor_EDGAR_File)
'         -> Opens file and imports data into Raw_data (Import_EDGAR_Data)
'
' INPUTS (from Raw_data Sheet):
'     H1  -> Ticker
'     H2  -> Fiscal Year (YYYY)
'     H3  -> Quarter (1Ð4)
'     H4  -> Full Year Mode (True/False; only valid if Quarter = 4)
'     G23 -> API Key (or "public")
'     G25 -> Download folder path
'     G27 -> Show download prompt (True/False)
'
' SHARED VARIABLES:
'     wbModel         -> ThisWorkbook reference
'     wsModel         -> "Raw_data" worksheet
'     wbDownloaded    -> Downloaded Excel workbook (opened at runtime)
'     filePath        -> Full download path to expected .xlsx file
'
' PUBLIC PROCEDURES:
'     Run_Edgar_Update      -> Primary macro users should run to trigger full pipeline
'     SetupDownloadPath     -> Assembles download filename and path from user inputs
'
' PRIVATE PROCEDURES:
'     SetupRanges           -> Loads all configuration ranges from Raw_data
'     Get_EDGAR_Data        -> Builds URL and opens browser for API download trigger
'     WaitFor_EDGAR_File    -> Waits for downloaded file to appear in folder
'     Open_EDGAR_File       -> Opens file if not already open
'     Import_EDGAR_Data     -> Copies new values into Raw_data and deletes temp file
'
' FILE REQUIREMENTS:
'     - A valid .xlsx file exported by the EDGAR Flask API must be downloaded to the path in G25
'     - The downloaded file must contain a sheet named "Raw_data" with 5 columns (AÐE)
'
' COMPATIBILITY:
'     - Designed for Excel on Mac or Windows
'     - Tested with Office 365, 2021
'
' =======================================================================================

' === Shared variables for all EDGAR macros ===
Dim wsModel As Worksheet
Dim rngTicker As Range
Dim rngYear As Range
Dim rngQuarter As Range
Dim rngFullYearMode As Range
Dim rngApiKey As Range
Dim rngDownloadFolder As Range
Dim wbModel As Workbook
Dim wbDownloaded As Workbook

' === Download-related shared variables ===
Dim downloadFolder As String
Dim downloadName As String
Dim filePath As String
Dim showDownloadMsg As Boolean


' === MAIN MACRO: Call to Update Sheet ===

Public Sub GetData()
    On Error GoTo CancelHandler
    
    Call SetupRanges
    If Not CheckInputs() Then Exit Sub
    
    ' === Step 1: Trigger the download ===
    Call Get_EDGAR_Data

    ' === Step 2: Wait for the file to download ===
    If Not WaitFor_EDGAR_File Then Exit Sub

    ' === Step 3: Open the downloaded file if not already open ===
    Call Open_EDGAR_File

    ' === Step 4: Import the data into the model ===
    Call Import_EDGAR_Data
    
    With ThisWorkbook.Sheets("Raw_data") 'scroll updater view back to starting point for clean view
        .Activate
        .Range("G1").Select
    End With
    
    Exit Sub
    
CancelHandler:
        MsgBox "Macro cancelled or encountered an error:" & vbCrLf & _
               "Error " & Err.Number & ": " & Err.Description & vbCrLf & vbCrLf & _
               "Send us an email at support@henrychien.com with the error and we'll take a look.", _
               vbInformation, "Macro Stopped"
               
    Application.StatusBar = False

End Sub

' === Set up values to pull from macro for script ===

Private Sub SetupRanges()
    Set wbModel = ThisWorkbook
    Set wsModel = wbModel.Sheets("Raw_data")
    Set rngTicker = wsModel.Range("H1")
    Set rngYear = wsModel.Range("H2")
    Set rngQuarter = wsModel.Range("H3")
    Set rngFullYearMode = wsModel.Range("H4")
    Set rngApiKey = wsModel.Range("G13")
    Set rngDownloadFolder = wsModel.Range("G15")
    
    ' === Clean download path at source ===
    downloadFolder = Trim(rngDownloadFolder.Value)
    downloadFolder = Replace(downloadFolder, Chr(160), "") ' remove non-breaking spaces
    downloadFolder = Replace(downloadFolder, vbTab, "")    ' remove tabs

    ' Add trailing slash if missing
    If Right(downloadFolder, 1) <> "/" And Right(downloadFolder, 1) <> "\" Then
        downloadFolder = downloadFolder & Application.PathSeparator
    End If

    showDownloadMsg = (UCase(Trim(wsModel.Range("G17").Value)) = "TRUE")
End Sub


' === Validate inputs to use to request file ===

Private Function CheckInputs() As Boolean

Dim fullYearRaw As String
fullYearRaw = LCase(Trim(rngFullYearMode.Text))

    Call SetupRanges

    If Trim(rngTicker.Value) = "" Then
        MsgBox "Enter ticker in cell H1", vbQuestion, "Missing Input"
        CheckInputs = False
        Exit Function
    End If

    If Trim(rngYear.Value) = "" Or Not IsNumeric(rngYear.Value) Then
        MsgBox "Year in cell H2 must be a valid 4-digit number", vbQuestion
        CheckInputs = False
        Exit Function
    End If

    If Len(Trim(rngYear.Value)) <> 4 Then
        MsgBox "Fiscal year in cell H2 must be a 4-digit year, e.g. 2024", vbExclamation
        CheckInputs = False
        Exit Function
    End If


    If Trim(rngQuarter.Value) = "" Or Not IsNumeric(rngQuarter.Value) Then
        MsgBox "Quarter in cell H3 must be a number 1Ð4", vbQuestion
        CheckInputs = False
        Exit Function
    End If
    
    If CLng(rngQuarter.Value) < 1 Or CLng(rngQuarter.Value) > 4 Then
        MsgBox "Quarter in cell H3 must be a number between 1 and 4", vbExclamation
        CheckInputs = False
        Exit Function
    End If

    If Trim(rngApiKey.Value) = "" Then
        MsgBox "Enter your API key in H22. Use 'public' if unsure.", vbQuestion
        CheckInputs = False
        Exit Function
    End If

    If Trim(rngDownloadFolder.Value) = "" Then
        MsgBox "Enter your download folder path in H23", vbQuestion
        CheckInputs = False
        Exit Function
    End If
    
    If fullYearRaw <> "true" And fullYearRaw <> "false" Then
        MsgBox "Full Year Mode in cell H4 must be 'True' or 'False'", vbQuestion, "Please check inputs on sheet"
        CheckInputs = False
        Exit Function
    End If

    CheckInputs = True
End Function

' === Calls the WebUI to download data ===

Private Sub Get_EDGAR_Data()

    Call SetupRanges

    Dim ticker As String, year As String, quarter As String, apiKey As String
    Dim fullYearMode As String, fullYearRaw As String, downloadFolder As String
    Dim url As String

    ticker = Trim(rngTicker.Value)
    year = Trim(rngYear.Value)
    quarter = Trim(rngQuarter.Value)
    fullYearRaw = LCase(Trim(rngFullYearMode.Text))
    fullYearMode = LCase(Trim(rngFullYearMode.Value))
    
    ' === Force reset if full year mode is invalid with this quarter
    If fullYearMode = "true" And CLng(quarter) <> 4 Then
        rngFullYearMode.Value = "False"
        MsgBox "Full Year Mode was automatically set to 'False'." & vbCrLf & _
               "This mode is only valid when Quarter = 4. The download will continue now", vbInformation, "Correcting Full Year Mode"
        fullYearMode = "false"
    End If

    apiKey = Trim(rngApiKey.Value)

    url = "https://financialmodelupdater.com/trigger_pipeline?" & _
          "ticker=" & ticker & _
          "&year=" & year & _
          "&quarter=" & quarter & _
          "&key=" & apiKey & _
          "&full_year_mode=" & LCase(fullYearMode)

    If showDownloadMsg Then
        MsgBox "Excel will open your browser to download the updated data." & vbCrLf & _
               "It may take a minute. " & vbCrLf & _
               "If prompted, save the file to your Downloads folder." & vbCrLf & _
               "Switch back to Excel when done.", vbInformation, "Ready to Download"
    End If
    

    wbModel.FollowHyperlink address:=url
    Debug.Print "Trigger URL: " & url

End Sub

' === Setup download path per configuration ===
Private Sub SetupDownloadPath()

    Call SetupRanges

    Dim ticker As String, year As String, quarter As String, fullYearMode As String
    ticker = Trim(rngTicker.Value)
    year = Trim(rngYear.Value)
    quarter = Trim(rngQuarter.Value)
    fullYearMode = LCase(Trim(rngFullYearMode.Value))

    If fullYearMode = "true" Then
        downloadName = ticker & "_FY" & Right(year, 2) & "_Updater_EDGAR.xlsx"
    Else
        downloadName = ticker & "_" & quarter & "Q" & Right(year, 2) & "_Updater_EDGAR.xlsx"
    End If

    filePath = downloadFolder & downloadName

End Sub

'' === Opens the downloaded update file ===

Private Function WaitFor_EDGAR_File() As Boolean
    Call SetupRanges
    Call SetupDownloadPath

    Dim response As VbMsgBoxResult
    Dim fullPathExists As Boolean

    response = MsgBox( _
        "Is this file in your Downloads folder?" & vbCrLf & _
        "Expected file: " & downloadName & vbCrLf & _
        "Path: " & downloadFolder, _
        vbQuestion + vbYesNo, _
        "Confirm Download")

    If response = vbYes Then
        fullPathExists = (Dir(filePath) <> "")
        If Not fullPathExists Then
            MsgBox "Could not find:" & filePath & vbCrLf & _
                   "Please check the folder path and run again.", _
                   vbExclamation, "File Not Found"
            WaitFor_EDGAR_File = False
        Else
            WaitFor_EDGAR_File = True
        End If
    Else
        MsgBox "It may be large filing. Wait a minute then run the macro again.", vbInformation, "Process Cancelled"
        WaitFor_EDGAR_File = False
    End If
End Function

' === Opens the downloaded update file ===

Private Sub Open_EDGAR_File()

    Call SetupRanges
    Call SetupDownloadPath

    Dim wb As Workbook
    Dim alreadyOpen As Boolean
    alreadyOpen = False

    For Each wb In Workbooks
        If wb.Name = downloadName Then
            Set wbDownloaded = wb
            alreadyOpen = True
            Exit For
        End If
    Next wb

    If alreadyOpen Then Exit Sub

    If Dir(filePath) = "" Then
        MsgBox "No file found at:" & filePath & vbCrLf & _
               "Please check the folder path in cell H23 and make sure the download completed.", _
               vbInformation, "No File Detected"
        Exit Sub
    End If

    On Error Resume Next
    Set wbDownloaded = Workbooks.Open(filePath)
    On Error GoTo 0
    
    If wbDownloaded Is Nothing Then
        MsgBox "Could not open the downloaded file. Please check if it's been corrupted.", vbInformation, "Open Failed"
    Exit Sub
    End If

End Sub

' === Updates the updater file with new data ===

Private Sub Import_EDGAR_Data()

    Call SetupRanges
    Call SetupDownloadPath

    Dim wsDownloaded As Worksheet
    Dim wb As Workbook
    Dim weOpenedIt As Boolean

    If wbDownloaded Is Nothing Then
        For Each wb In Workbooks
            If wb.Name = downloadName Then
                Set wbDownloaded = wb
                Exit For
            End If
        Next wb
    End If

    If wbDownloaded Is Nothing Then
        If Dir(filePath) = "" Then
            MsgBox "No file found at:" & filePath & vbCrLf & _
                   "Please check the folder path in cell H23 and make sure the download completed.", _
                   vbInformation, "No File Detected"
            Exit Sub
        End If
        Set wbDownloaded = Workbooks.Open(filePath)
        weOpenedIt = True
    End If

    On Error Resume Next
    Set wsDownloaded = wbDownloaded.Sheets("Raw_data")
    On Error GoTo 0
    
    If wsDownloaded Is Nothing Then
        MsgBox "'Raw_data' sheet not found in downloaded file.", vbInformation, "Import Failed"
        Exit Sub
    End If

    wsModel.Range("A1:E1000").ClearContents
    wsDownloaded.Range("A1:E1000").Copy
    wsModel.Range("A1").PasteSpecial xlPasteValues
    Application.CutCopyMode = False

    If Not wbDownloaded Is Nothing Then
        wbDownloaded.Close SaveChanges:=False
        Set wbDownloaded = Nothing
    End If
    
    On Error Resume Next
    Kill filePath ' deletes file
    On Error GoTo 0

    MsgBox "Data updated in: " & wbModel.Name & " | Sheet: " & wsModel.Name & vbCrLf & _
     "Run Update Model to update your model!", _
     vbInformation, "Ready to Use!"

End Sub


