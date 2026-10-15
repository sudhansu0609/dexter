@echo off
rem Start the ai-visualizer "face" sidecar (jaredrhod, AGPL) that reads Dexter's
rem .voice_state / .voice_waveform bus files and renders the animated face.
rem
rem This only launches the sidecar's own run.bat (interpreter discovery lives
rem there); it does not modify any AGPL file. The face is embedded in Dexter's UI
rem via src/components/FacePanel.tsx (iframe to http://127.0.0.1:8790/), and the
rem backend feeds it through POST /api/dexter/voice-bus. Port + name are set in
rem jaredRepo\ai-visualizer\ai-visualizer.json.
setlocal
cd /d "%~dp0..\jaredRepo\ai-visualizer" || (echo ai-visualizer folder not found & exit /b 1)
call run.bat %*
endlocal
