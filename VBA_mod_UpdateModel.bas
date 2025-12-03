Attribute VB_Name = "mod_UpdateModel"

' ============================================
' === Update Model Macro Overview ===
' Author: Henry Chien
' Email: support@yhenrychien.com
' ============================================

' What it does:
' This macro copies over the formulas and formats from a source range to a target
' range in your financial model. It then matches values from the updater sheet
' and writes the corresponding values into the target cells.
'
' Supports two modes:
'   - NORMAL MODE (default): Match prior-year values, write current-year values
'   - REVERSE MODE: Match current-year values, write prior-year values (for backfilling)

' Usage:
'   1. Ensure the Updater sheet is filled with updated current and prior year values
'       (you can use the integrated Edgar extractor to generate this automatically)
'   2. Set Reverse Mode in cell G19 (TRUE = reverse, FALSE/blank = normal)
'   3. Run the macro and select the source range (values to match against)
'   4. Select the target starting cell (where to write matched values)
'   5. Review the updated values, including any highlighted missing cells
'       or potential duplicates.
'
' Last updated: 2025-12-02
' ============================================

' =======================================================================================
' MODULE:      EDGAR Model Updater

' DESCRIPTION:
'     Automates the process of updating a financial model with new values extracted from
'     SEC filings. It matches constants and formulas from a prior period column to current
'     period values pulled from EDGAR using an external extractor.
'
' FEATURES:
'     - Copies formats and formulas from source to target column
'     - Supports Normal and Reverse modes for flexible updating direction
'     - Matches constants and formulas with optional sign flipping
'     - Applies formatting to highlight collisions or mismatches
'     - Clears updater sheet with a single macro
'
' DEPENDENCIES:
'     Sheet "Raw_data" must be present
'     Requires external tool to populate updater sheet (e.g., EDGAR extractor)
'
' PUBLIC PROCEDURES:
'     - UpdateModel: Main macro to update values/formulas in financial model
'     - ClearUpdater: Clears all inputs/outputs from Raw_data sheet
'
' PRIVATE UTILITIES:
'     - CopyLast: Copies formulas/formats from prior to current range
'     - HighlightCell: Flags a cell with bold yellow formatting
'     - BreakDownFormula: Tokenizes a formula string into parts
'     - BuildFormula: Reconstructs a formula from parts
'     - ConvertSign: Reverses sign of a value
'     - ConvertData: (not shown, assumed similar to ConvertDataBack)
'     - ConvertDataBack: Reverses conversion factor scaling
'
' =======================================================================================

' =======================================================================================
' === MAIN MACRO: UpdateModel() ===

' Description:
'     Updates a selected column in a financial model using mapped prior/current values from
'     the Updater sheet. The macro matches constants and formula components based on numeric
'     similarity, including optional sign flipping, and visually flags mismatches or collisions.
'
' Modes:
'     - NORMAL (G19 = FALSE/blank): Search priorArray, write from currentArray
'       Use case: Updating current year column using prior year as reference
'     - REVERSE (G19 = TRUE): Search currentArray, write from priorArray
'       Use case: Backfilling prior year column using current year as reference
'
' Core Workflow:
'     1. Read mode setting from G19 (reverse mode on/off)
'     2. User selects source column (values to match against)
'     3. User selects target starting cell (where to write matched values)
'     4. Macro copies formulas/formats from source to target, then:
'         - Matches numeric constants against searchArray
'         - Rewrites formulas by replacing matched terms with writeArray values
'         - Applies flags for unmatched/multi-matched values (collisions)
'
' Features:
'     - Auto-cancels if collision rate is high and user declines
'     - Skips error/blank cells and common formula constants ("1", "0", "-1")
'     - Highlights suspicious matches using bold and fill color
'     - Handles merged cell and range selection errors
'
' Inputs:
'     - User-selected source and target ranges (1 column each)
'     - Updater sheet (default: "Raw_data") with columns:
'         B: current values
'         C: prior values
'         E: collision marker
'
' Globals / Assumptions:
'     - Workbook must include a sheet named "Raw_data"
'     - Cell G11 contains conversion factor (e.g. /1000)
'     - Cell G19 contains reverse mode setting (TRUE/FALSE)
'     - Cell G22 contains pre-calculated collision rate
'     - Helper functions: ConvertData(), ConvertSign(), ConvertDataBack(),
'       BreakDownFormula(), BuildFormula(), HighlightCell()
'
' Output:
'     - Updates target column in place
'     - Flags collisions and unmatched values visually
'     - Summary messages for completion and error handling
'
' Example Usage:
'     Set G19 to TRUE for reverse mode, run macro, select source and target ranges
'
' =======================================================================================

Public Sub UpdateModel()

' === INITIALIZATION & RAW DATA SETUP ======================================

Dim currentStage As String
Dim successfulRun As Boolean
Dim reverseMode As Boolean
currentStage = "Initializing"
successfulRun = False

On Error GoTo ExitGracefully

'Dim my_FileName As Variant
Dim raw_sheetName As Variant

'my_FileName = "Updater_Edgar.xlsm" 'file name of the workbook
raw_sheetName = "Raw_data" 'updater sheet

Dim MatchType As Long
Dim IndexColumn As Long
Dim Conversion_factor As Double
Dim targetValue As Double
Dim matchKey As Variant
Dim startRange As String

Dim ws_Raw As Worksheet

Set ws_Raw = ThisWorkbook.Sheets(raw_sheetName)

' Control settings with input from excel sheet
MatchType = 0 'sets the match type to be 0 to get exact match only for safety
IndexColumn = 1 'sets the index to pull the value from first column of updater range (column B = current period)
Conversion_factor = Val(ws_Raw.Range("G11").Value) ''set the conversion factor which is configurable


' === COLLISION WARNING ======================================

Dim estimatedCollisionRate As Double
estimatedCollisionRate = 0 ' Default fallback

On Error Resume Next ' Gracefully handle empty or missing cell
estimatedCollisionRate = ws_Raw.Range("G22").Value
On Error GoTo ExitGracefully

If estimatedCollisionRate >= 0.4 Then
    Dim earlyWarning As VbMsgBoxResult
    earlyWarning = MsgBox("Collision rate is " & Format(estimatedCollisionRate, "0.0%") & "." & vbCrLf & _
                          "This indicates many values were not cleanly matched between current and prior periods." & vbCrLf & _
                          "(Often due to restatements, disclosure changes, custom tagging structures etc.)" & vbCrLf & _
                          "The matches are likely OK, but you may see many cells highlighted in your model flagged for review." & vbCrLf & _
                          vbCrLf & "Recommend saving your work and turning off AutoSave before continuing." & vbCrLf & _
                          "Do you want to proceed?", vbYesNo + vbExclamation, "High Collision Rate Warning")

    If earlyWarning = vbNo Then
        MsgBox "Macro safely cancelled." & vbCrLf & _
        "Kindly let us know if you'd like us to review this company." & vbCrLf & _
        "Send us an email at support@henrychien.com.", _
               vbInformation, "Update Cancelled"
        Exit Sub
    End If
End If

' === LOAD RAW DATA INTO ARRAYS ============================================

Dim currentArray() As Variant
Dim priorArray() As Variant
Dim lastRow As Long

lastRow = ws_Raw.Cells(ws_Raw.Rows.Count, "C").End(xlUp).Row 'get last row of prior period values in updater to set arrays

Dim i As Long
ReDim currentArray(1 To lastRow)
ReDim priorArray(1 To lastRow)
ReDim collisionArray(1 To lastRow)

For i = 2 To lastRow 'start at row 2 (skip header) and build arrays
    currentArray(i) = ws_Raw.Cells(i, 2).Value         ' Column B = current year
    priorArray(i) = ws_Raw.Cells(i, 3).Value           ' Column C = prior year
    collisionArray(i) = ws_Raw.Cells(i, 5).Value       ' Column E = collision marker
Next i

' === DECLARE SEARCH/WRITE ARRAYS FOR MODE-AGNOSTIC MATCHING ===
Dim searchArray() As Variant
Dim writeArray() As Variant

' === READ REVERSE MODE SETTING FROM CELL ===
' Cell G19: TRUE/1 = Reverse mode, FALSE/0/blank = Normal mode
reverseMode = False  ' Default to normal mode
On Error Resume Next
reverseMode = CBool(ws_Raw.Range("G19").Value)
On Error GoTo ExitGracefully

' === ASSIGN SEARCH/WRITE ARRAYS BASED ON MODE ===
If reverseMode Then
    ' REVERSE MODE: Search in current period, write from prior period
    searchArray = currentArray  ' Column B (current year values)
    writeArray = priorArray     ' Column C (prior year values)
    Debug.Print "Running in REVERSE MODE: searching current values, writing prior values"
Else
    ' NORMAL MODE: Search in prior period, write from current period
    searchArray = priorArray    ' Column C (prior year values)
    writeArray = currentArray   ' Column B (current year values)
    Debug.Print "Running in NORMAL MODE: searching prior values, writing current values"
End If

' === SELECT RANGES TO UPDATE ==============================================

currentStage = "Setting Ranges"

Dim updateRange As Range
Dim address As String

'Select the source range to refer to for the updater to match
Dim sourcePrompt As String
If reverseMode Then
    sourcePrompt = "Select the CURRENT YEAR's range of values (to be matched)."
Else
    sourcePrompt = "Select the PRIOR YEAR's range of values (to be matched)."
End If

SelectUpdateRange:
DoEvents '' Allow user to switch to their financial model before selecting range
Set updateRange = Application.InputBox( _
    prompt:="Reminder: Save before running and turn off AutoSave. Press Esc to cancel." & vbCrLf & _
           sourcePrompt & " Tip: Hold shift to select full range.", _
    Type:=8)
    
'Block to prevent script using multi-column range
If updateRange.Columns.Count > 1 Then
    MsgBox "Please select only one column." & vbCrLf & _
    "Multi-column ranges will result in errors.", vbExclamation
    GoTo SelectUpdateRange
End If

'Block to prevent script running on merged cells
Dim cell As Range
For Each cell In updateRange
    If cell.MergeCells Then
        MsgBox "Merged cell detected at " & cell.address & "." & vbCrLf & _
            "Please unmerge before proceeding.", vbExclamation
        Exit Sub
    End If
Next cell
    
NumRows = updateRange.Rows.Count 'count the number of rows for the loop
address = updateRange.Cells(1, 1).address ' get the address of first cell in updateRange as reference

Dim currentRange As Range

'Select the target range starting point to input new values
Dim targetPrompt As String
  If reverseMode Then
      targetPrompt = "Select the *first cell* of the PRIOR YEAR's range (to be updated)."
  Else
      targetPrompt = "Select the *first cell* of the CURRENT YEAR's range (to be updated)."
  End If


SelectCurrentRange:
DoEvents  'Allow Excel to process user switching back to their model
Set currentRange = Application.InputBox( _
    prompt:="FYI: You selected range starting at " & address & vbCrLf & _
            targetPrompt, _
    Type:=8)
    
'Block to prevent selection on more than one cell (will cause errors)
If currentRange.Columns.Count > 1 Then
    MsgBox "Please select only the first cell of the range to update.", vbExclamation
    GoTo SelectCurrentRange
End If

'Check if the selected current year's range is different row (could be mistake)
If updateRange.Row <> currentRange.Row Then
    Dim userResponse As VbMsgBoxResult
    userResponse = MsgBox("The selected ranges start on different rows." & vbCrLf & _
                          "Do you want to continue?", vbQuestion + vbYesNo, "Row Difference Warning")
    If userResponse = vbNo Then
        GoTo SelectCurrentRange
    End If
End If

'Calculates current year's range using the prior year's range
Dim fullCurrentRange As Range
Set fullCurrentRange = Range(currentRange.Cells(1, 1), currentRange.Cells(1, 1).Offset(NumRows - 1, 0))

' Block if there's merged cells in the calculated current year's range
For Each cell In fullCurrentRange
    If cell.MergeCells Then
        MsgBox "Merged cell detected in current year range at " & cell.address & "." & vbCrLf & _
               "Please unmerge cells in column to avoid errors.", vbExclamation
        Exit Sub
    End If
Next cell

' Block if there's the selected prior year's range is the same as the current year's range
If Not Intersect(updateRange, currentRange) Is Nothing Then
    MsgBox "Your selected ranges are the same." & vbCrLf & _
           "Please reselect non-overlapping ranges to avoid overwriting data.", vbExclamation
    Exit Sub
End If

Call CopyLast(updateRange, currentRange) 'to copy over the formats and formulas from last year's range to current year's range
currentStage = "After range selection"

' === BEGIN MATCHING LOOP ===================================================
'This loop goes the copied over prior year values and replaces them with current year values from the updater sheet.

'Disable  auto-calculations and stop any potential macros
Application.Calculation = xlCalculationManual
Application.EnableEvents = False

currentStage = "Starting update loop"
startRange = currentRange.Cells(1, 1).address 'address of the first cell in the update range
currentRange.Worksheet.Activate
currentRange.Worksheet.Range(startRange).Select 'select the starting point for the loop

Dim sourceValue As Double 'value from source range cell to match against searchArray
Dim args As Variant 'variant to store the array
Dim active_cell As String 'declare string to manipulate the formula as string
Dim matchedColor As Long
Dim EnableFlippedMatch As Boolean
Dim found As Boolean
Dim collisionCount As Integer
Dim matchedVal As Variant
Dim x As Integer

collisionCount = 0
found = False
EnableFlippedMatch = True
matchedColor = RGB(50, 50, 50) 'color to mark data that has been matched in updater sheet

For x = 1 To NumRows 'loop through the range based on number of rows
    If IsError(ActiveCell) = False Then 'skip error cells
        If IsEmpty(ActiveCell.Value) = True Or ActiveCell.Value = 0 Or IsNumeric(ActiveCell) = False Then 'skip empty, "0", or non-number cells
            Else
                currentStage = "Matching constants"
                
' === MATCHING LOGIC FOR CONSTANTS =====================
                
            If ActiveCell.HasFormula = False Then
                sourceValue = ConvertData(ActiveCell.Value, Conversion_factor) 'see function to convert data
                found = False

                ' Constants match against updater prior/current arrays (checks with sign flipped if EnabledFlippedMatch = true)
                
                For j = 1 To UBound(searchArray, 1)
                    If IsNumeric(searchArray(j)) And IsNumeric(writeArray(j)) And writeArray(j) <> 0 Then
                        If Round(searchArray(j), 0) = Round(sourceValue, 0) Then
                            targetValue = ConvertDataBack(writeArray(j), Conversion_factor)
                            found = True
                            Exit For
                        ElseIf EnableFlippedMatch And Round(searchArray(j), 0) = Round(ConvertSign(sourceValue), 0) Then 'flip sign of current if matched with flipped sign for prior
                            targetValue = ConvertSign(ConvertDataBack(writeArray(j), Conversion_factor))
                            found = True
                            Exit For
                        End If
                    End If
                Next j
                
                If found Then
                        ActiveCell = targetValue     'Writes new value into cell
                            If collisionArray(j) = 1 Then
                                Call HighlightCell(ActiveCell)  ' Mark potential duplicates yellow + bold based on collision flag
                                collisionCount = collisionCount + 1
                            End If
 
                            Else
                                ActiveCell.ClearContents 'Clear cell if no match
                                ActiveCell.Interior.ColorIndex = 6  ' Mark non-matches cell yellow
                    End If
                Else '
                
' === MATCHING LOGIC FOR FORMULAS ========
'Script will breakdown the formula into a simple array then loop through each numeric element to check if exists in prior array

                currentStage = "Breaking down formulas"
                args = BreakDownFormula(ActiveCell.formula) 'function to breakdown formula into tokens to check
                Dim dbg As String
                dbg = "Formula parts after BreakDownFormula:" 'debug message to review formula tokens
                
                For i = LBound(args) To UBound(args)
                    dbg = dbg & vbCrLf & "args(" & i & ") = '" & args(i) & "'"
                Next i
                Debug.Print dbg 'debug message to review each token
    
                    For i = LBound(args) To UBound(args)
                     
                     Debug.Print "TOKEN = '" & args(i) & "' | IsNumeric = " & IsNumeric(args(i)) 'debug to check individual elements checked
                     
                    If IsNumeric((args(i))) = True Then
                        If args(i) <> "1" And args(i) <> "-1" And args(i) <> "0" Then  'exclude "1" since often used in percentage formula (x/y-1) - note: will replace things like"365"
                        
                            Dim Count As Integer
                            Count = 1
                            Dim matchedFlipped As Boolean
                            matchedFlipped = False
                        
                        For Count = 1 To 2 'loop twice (once with "-" and once with "+" sign to check for matches)
                            If Count = 1 Then
                                sourceValue = ConvertData(args(i), Conversion_factor) 'transforming the token to check with search array
                                Else
                                    sourceValue = ConvertSign(ConvertData(args(i), Conversion_factor)) 'check with different sign
                            End If

                            matchedVal = args(i) 'individual token to match

                            'Debug to check what matched - if it's flipped or not
                            Debug.Print "Matching raw value: " & CStr(matchedVal) & _
                                        " | Converted: " & CStr(ConvertData(matchedVal, Conversion_factor)) & _
                                        " | Flipped: " & CStr(ConvertSign(ConvertData(matchedVal, Conversion_factor)))

                            matchKey = Application.Match(Round(sourceValue, 0), searchArray, MatchType) 'match functionality to compare with search array values in updater sheet

                                If IsError(matchKey) = False Then
                                    targetValue = ConvertDataBack(writeArray(matchKey), Conversion_factor)  'Use conversion function to transform raw data to value to input
                                        If Count = 2 Then
                                            matchedFlipped = True
                                            targetValue = ConvertSign(targetValue)  'Write the new value into cell in financial model
                                        End If

                                    args(i) = Format$(targetValue, "+0.###;-0.###")  'format value being writing it back into formula

                                    'Debug messages to check target value and how it's transformed

                                    Debug.Print "target value (args[" & i & "]): " & CStr(args(i)) & _
                                    " | MatchKey: " & CStr(matchKey) & _
                                    " | Used flipped match? Count=" & CStr(Count) & _
                                    " | Final target val to insert: " & CStr(targetValue)

                                    ActiveCell.formula = BuildFormula(args) 'rebuild the cell's formula with new values

                                        If collisionArray(matchKey) = 1 Then 'check for collision flag for that matched row index
                                                Call HighlightCell(ActiveCell)
                                                collisionCount = collisionCount + 1  ' Increment collision count
                                        End If
                                    Count = 2 'end loop early with match

                                    Else
                                        If Count = 2 Then
                                            targetValue = 0 'replace with "0" if no match
                                            args(i) = targetValue

                                        'Debug messages to check non-matched values

                                        Debug.Print "target value (args[" & i & "]): " & CStr(args(i)) & _
                                                    " | MatchKey: " & CStr(matchKey) & _
                                                    " | Used flipped match? Count=" & CStr(Count) & _
                                                    " | Final target val to insert: " & CStr(targetValue)

                                        ActiveCell.formula = BuildFormula(args)

                                        Else
                                        End If
                                    End If
                                Next Count
                            End If
                    End If
                Next i
            End If
        End If
    End If
    ActiveCell.Offset(1, 0).Select 'go one row down to continue with loop
Next
    
' === EXIT SCRIPT WITH MESSAGES ===================================================

    If collisionCount > 0 Then
        MsgBox "Attention: " & collisionCount & " flagged value(s) bolded for potential mismatches." & vbCrLf & _
               "Please check highlighted cells for missing values or mismatches." & vbCrLf & _
               "Reminder: Check the filing for new disclosures or restatements", _
               vbInformation, "Model Update Complete :)"
    Else 'success message: Can mention the log for new entries (please checklog sheet for new entries in this period's filings)
         MsgBox "No duplicate values detected." & vbCrLf & _
         "Please check highlighted cells for missing values." & vbCrLf & _
        "Reminder: Check the filing for new disclosures or restatements", _
        vbInformation, "Model Update Complete :)"
    End If
    
    With ThisWorkbook.Sheets("Raw_data") 'scroll updater view back to starting point for clean view
        .Activate
        .Range("G1").Select
    End With
    
    currentRange.Parent.Parent.Activate ' Activate the workbook (financial model)
    Application.GoTo Reference:=currentRange.Cells(1, 1) 'go to starting point
    
    successfulRun = True

'turn back on automatic calculations and events
Application.Calculation = xlCalculationAutomatic
Application.EnableEvents = True
Exit Sub
    
ExitGracefully:
    If currentStage <> "" Then Debug.Print "Crashed at stage: " & currentStage
        If currentStage = "Setting Ranges" Then
            MsgBox "Macro stopped. Exiting safely.", vbInformation, "Macro Exit"
        ElseIf Err.Number <> 0 Then
            MsgBox "Macro ran into an error." & vbCrLf & vbCrLf & _
                   "Error " & Err.Number & ": " & Err.Description & vbCrLf & vbCrLf & _
                   "Stage: " & currentStage & vbCrLf & _
                   "Please copy and send this message to support.", _
                   vbExclamation, "Macro Exit"
        Else
            MsgBox "Macro stopped. Exiting safely.", vbInformation, "Macro Exit"
        End If
        
    'turn back on automatic calculations and events
    Application.Calculation = xlCalculationAutomatic
    Application.EnableEvents = True
    End Sub
    
' =======================================================================================
' Subroutine:  ClearUpdater
' Scope:       Public (can be called from buttons or menus)
'
' Description:
'     Clears all content and formatting from the Raw_data sheet in the updater file,
'     including current/prior year values, descriptions, presentation roles, and collision flags.
'     Useful for resetting the sheet between EDGAR-based updates.
'
' Behavior:
'     - Targets columns A?E (Description, Current Year, Prior Year, Role, Collision Flag)
'     - Clears cell contents
'     - Removes formatting (fill color, font styles, borders, number format)
'
' Affected Ranges:
'     - Column A: Descriptions
'     - Column B: Current Year Values
'     - Column C: Prior Year Values
'     - Column D: Presentation Roles
'     - Column E: Collision Flags
'
' Globals / Assumptions:
'     - Active workbook contains a sheet named "Raw_data"
'     - Column A contains sequential rows without large gaps
'     - Last row is calculated using column A
'
' Example:
'     Call ClearUpdater() to wipe all working data from the updater before reloading new extractions.
'
' =======================================================================================

Private Sub ClearUpdater()

'Dim my_FileName As Variant
Dim raw_sheetName As Variant

'my_FileName = "Updater_Edgar.xlsm" 'this is the file name of the workbook
raw_sheetName = "Raw_data" 'this is the sheet

Dim ws_Raw As Worksheet
Dim currentYearRangeValues As Range
Dim priorYearRangeValues As Range
Dim descriptionRange
Dim lastRow As Long
Dim cell As Range
Dim adjacentCell As Range

Dim presentationRoleValues As Range
Dim collisionFlagValues As Range

Set ws_Raw = ThisWorkbook.Sheets(raw_sheetName)

  ' Find the last non-empty row in column A using End(xlUp)
    lastRow = ws_Raw.Cells(ws_Raw.Rows.Count, "A").End(xlUp).Row
    
Set currentYearRangeValues = ws_Raw.Range("B2:B" & lastRow) 'set range with current year values
Set priorYearRangeValues = ws_Raw.Range("C2:C" & lastRow) 'set range with prior year values
Set descriptionRange = ws_Raw.Range("A2:A" & lastRow) 'set range with prior year values
Set presentationRoleValues = ws_Raw.Range("D2:D" & lastRow) 'set range with presentation role values
Set collisionFlagValues = ws_Raw.Range("E2:E" & lastRow) 'set range with collision flag values

With collisionFlagValues 'clear all formats

        .Interior.ColorIndex = xlNone   ' Clear any fill colors
        .Font.Bold = False              ' Remove bold formatting
        .Font.ColorIndex = xlAutomatic  ' Reset font color to default
        .Borders.LineStyle = xlNone     ' Remove borders
        .NumberFormat = "General"       ' Reset number format
        .ClearContents 'Clear cell contents
        
    End With

With presentationRoleValues 'clear all formats

        .Interior.ColorIndex = xlNone   ' Clear any fill colors
        .Font.Bold = False              ' Remove bold formatting
        .Font.ColorIndex = xlAutomatic  ' Reset font color to default
        .Borders.LineStyle = xlNone     ' Remove borders
        .NumberFormat = "General"       ' Reset number format
        .ClearContents 'Clear cell contents
        
    End With

With currentYearRangeValues 'clear all formats

        .Interior.ColorIndex = xlNone   ' Clear any fill colors
        .Font.Bold = False              ' Remove bold formatting
        .Font.ColorIndex = xlAutomatic  ' Reset font color to default
        .Borders.LineStyle = xlNone     ' Remove borders
        .NumberFormat = "General"       ' Reset number format
        .ClearContents 'Clear cell contents
        
    End With

With priorYearRangeValues 'clear all formats

        .Interior.ColorIndex = xlNone   ' Clear any fill colors
        .Font.Bold = False              ' Remove bold formatting
        .Font.ColorIndex = xlAutomatic  ' Reset font color to default
        .Borders.LineStyle = xlNone     ' Remove borders
        .NumberFormat = "General"       ' Reset number format
        .ClearContents 'Clear cell contents
        
    End With
     
With descriptionRange 'clear all formats

        .Interior.ColorIndex = xlNone   ' Clear any fill colors
        .Font.Bold = False              ' Remove bold formatting
        .Font.ColorIndex = xlAutomatic  ' Reset font color to default
        .Borders.LineStyle = xlNone     ' Remove borders
        .NumberFormat = "General"       ' Reset number format
        .ClearContents 'Clear cell contents
        
    End With
    
    Dim flagRange As Range
Set flagRange = ws_Raw.Range("D2:D" & lastRow)

With flagRange
    .Interior.ColorIndex = xlNone   ' Clear any fill colors
    .Font.Bold = False              ' Remove bold formatting
    .Font.ColorIndex = xlAutomatic  ' Reset font color to default
    .Borders.LineStyle = xlNone     ' Remove borders
    .NumberFormat = "General"       ' Reset number format
    .ClearContents 'Clear cell contents
End With

End Sub

' === HELPER FUNCTIONS ===

' =======================================================================================
' Function:    ConvertDataBack
' Scope:       Private
'
' Description:
'     Reverses the scaling transformation applied to numeric data using a conversion factor.
'     Commonly used to convert display-friendly numbers (e.g., in thousands) back to raw values.
'
' Args:
'     data (Variant): The transformed numeric value to reverse (e.g., 12.3).
'     Conversion_factor (Double): The scaling factor to reverse (e.g., 1000).
'
' Returns:
'     Variant: The unscaled numeric value (e.g., 12,300). Returns 0 if input is non-numeric.
'
' Example:
'     ConvertDataBack(12.3, 1000) ? 12300
'
' Use Case:
'     Used during the UpdateModel loop to reapply original financial scale when writing
'     updater values back into the Excel model.
'
' =======================================================================================

Private Function ConvertData(data As Variant, Conversion_factor As Double)
    ConvertData = (data * Conversion_factor)
End Function

' =======================================================================================
' Function:    ConvertDataBack
' Scope:       Private
'
' Description:
'     Reverses a previously applied numeric transformation by dividing the input by the
'     given conversion factor. Commonly used to convert scaled values (e.g., in thousands)
'     back to raw amounts before inserting into the model.
'
' Args:
'     data (Variant): A numeric value previously scaled (e.g., 12.3 for 12,300).
'     Conversion_factor (Double): The factor used during the original conversion (e.g., 1000).
'
' Returns:
'     Variant: The original unscaled value (e.g., 12,300). Returns 0 for non-numeric inputs.
'
' Example:
'     ConvertDataBack(12.3, 1000) ? 12300
'
' Use Case:
'     Called during UpdateModel when writing new values into formulas or constants
'     after matching with the updater sheet.
'
' =======================================================================================

Private Function ConvertDataBack(data As Variant, Conversion_factor As Double)
    If IsNumeric(data) = True Then
        ConvertDataBack = (data / Conversion_factor) 'conversion reversed by /1000
    Else
        ConvertDataBack = 0
        End If
End Function

' =======================================================================================
' Function:    ConvertSign
' Scope:       Private
'
' Description:
'     Reverses the sign of a numeric value. Converts positive numbers to negative,
'     and vice versa.
'
' Args:
'     data (Variant): A numeric value to flip (e.g., 100 ? -100).
'
' Returns:
'     Variant: The input multiplied by -1. Non-numeric inputs may result in type mismatch.
'
' Example:
'     ConvertSign(25)    ? -25
'     ConvertSign(-100)  ? 100
'
' Use Case:
'     Used when checking for flipped-sign matches between current and prior year values
'     (e.g., expenses reported as negatives vs positives).
'
' ======================================================================================

Private Function ConvertSign(data)
    ConvertSign = (data * -1)
End Function

' =======================================================================================
' Function:    BreakDownFormula
' Scope:       Private
'
' Description:
'     Parses an Excel-style formula string into discrete tokens (numbers, operators, and references)
'     for inspection and substitution. This enables value-matching and transformation inside formulas.
'
' Behavior:
'     - Removes leading "=" from formulas
'     - Breaks formulas into tokens by detecting operators: +, -, *, /, (, ), =
'     - Distinguishes between standalone signs and negative numbers (e.g., "-100" vs "-")
'     - Ignores whitespace and maintains order of expression elements
'
' Args:
'     formula (String): A raw Excel formula string (with or without a leading "=")
'
' Returns:
'     Variant (Array of String): Ordered list of tokenized elements (e.g., ["(", "G7", "/", "F7", "-", "1", ")"])
'
' Example:
'     Input:  "=G7/F7-1"
'     Output: Array("G7", "/", "F7", "-", "1")
'
' Dependencies:
'     None ? purely string parsing logic.
'
' Use Case:
'     Used in conjunction with token-by-token replacement workflows for rebuilding financial formulas
'     with updated values or signs (see `BuildFormula` and update loops).
'
' =======================================================================================

Private Function BreakDownFormula(formula As String) As Variant

    Dim tokens() As String
    Dim i As Long, ch As String, token As String
    Dim pos As Long
    Dim c As String
    Dim sign As String

    ' Remove the leading "=" if present
    If Left(formula, 1) = "=" Then formula = Mid(formula, 2)

    ' Initialize array
    ReDim tokens(0)

    pos = 1
    Do While pos <= Len(formula)
        c = Mid(formula, pos, 1)

        Select Case c
            Case "+", "-"
                ' Flush existing token
                If token <> "" Then
                    tokens(UBound(tokens)) = token
                    ReDim Preserve tokens(UBound(tokens) + 1)
                    token = ""
                End If
                sign = c

                ' Peek next char
                If pos + 1 <= Len(formula) Then
                    If Mid(formula, pos + 1, 1) Like "[!$0-9A-Z]" Then
                        token = sign
                        pos = pos + 1
                        Do While pos <= Len(formula) And Mid(formula, pos, 1) Like "[!$0-9A-Z.]"
                            token = token & Mid(formula, pos, 1)
                            pos = pos + 1
                        Loop
                        tokens(UBound(tokens)) = token
                        ReDim Preserve tokens(UBound(tokens) + 1)
                        token = ""
                        GoTo NextChar
                    End If
                End If

                ' If sign not part of value, treat as operator
                tokens(UBound(tokens)) = c
                ReDim Preserve tokens(UBound(tokens) + 1)

            Case "*", "/", "(", ")", "="
                If token <> "" Then
                    tokens(UBound(tokens)) = token
                    ReDim Preserve tokens(UBound(tokens) + 1)
                    token = ""
                End If
                tokens(UBound(tokens)) = c
                ReDim Preserve tokens(UBound(tokens) + 1)

            Case Else
                token = token & c
        End Select

        pos = pos + 1
NextChar:
    Loop

    If token <> "" Then
        tokens(UBound(tokens)) = token
    ElseIf UBound(tokens) > 0 And tokens(UBound(tokens)) = "" Then
        ReDim Preserve tokens(UBound(tokens) - 1) ' clean up last blank slot
    End If

    BreakDownFormula = tokens
End Function

' =======================================================================================
' Function:    BuildFormula
' Scope:       Private
'
' Description:
'     Reconstructs an Excel formula string from a tokenized array of formula parts.
'     This is the inverse of `BreakDownFormula` and is used to write formulas
'     back into cells after substitution or transformation.
'
' Args:
'     data (Variant): Array of tokens (e.g., {"G7", "/", "F7", "-", "1"})
'
' Returns:
'     String: A full Excel formula string, prefixed with "="
'             (e.g., "=G7/F7-1")
'
' Example:
'     BuildFormula(Array("G7", "/", "F7", "-", "1")) ? "=G7/F7-1"
'
' Use Case:
'     Used during UpdateModel to rebuild cell formulas after inserting matched values.
'
' =======================================================================================

Private Function BuildFormula(data As Variant) As String
    BuildFormula = "=" & Join(data, "")
End Function

' =======================================================================================
' Subroutine:  HighlightCell
' Scope:       Private
'
' Description:
'     Applies a visual style to a given cell to flag it for review.
'     Used to indicate a potential collision or duplicate match.
'
' Behavior:
'     - Sets cell fill color to yellow (`ColorIndex = 6`)
'     - Applies bold font styling
'
' Args:
'     ActiveCell (Range): A single cell to be visually highlighted.
'
' Example:
'     Call HighlightCell(ActiveCell)
'
' Use Case:
'     Triggered during the UpdateModel matching loop to visually flag
'     cells that matched but had collision risk (e.g., many-to-one mapping).
'
' =======================================================================================

Private Sub HighlightCell(ActiveCell As Range)
    ActiveCell.Interior.ColorIndex = 6
    ActiveCell.Font.Bold = True
    
End Sub

' =======================================================================================
' Subroutine:  CopyLast
' Scope:       Private
'
' Description:
'     Copies formatting and formulas from the prior year?s range (`updateRange`) into the
'     current year?s range (`currentRange`) in a financial model.
'
' Behavior:
'     - Copies cell formats (e.g., number formats, fonts, colors, borders)
'     - Copies formulas (without values) from the source range to the target range
'     - Leaves cell values blank (assumes values will be overwritten by matching logic later)
'
' Args:
'     updateRange (Range): The prior period range to copy from (e.g., last year?s column).
'     currentRange (Range): The current period starting range to paste into.
'
' Example:
'     Call CopyLast(Range("D5:D50"), Range("E5"))
'
' Use Case:
'     Called at the beginning of UpdateModel to preserve layout and formula structure
'     before overwriting values using mapped updater data.
'
' =======================================================================================

Private Sub CopyLast(updateRange As Range, currentRange As Range)

updateRange.Copy
currentRange.PasteSpecial xlPasteFormats
currentRange.PasteSpecial xlPasteFormulas

End Sub







