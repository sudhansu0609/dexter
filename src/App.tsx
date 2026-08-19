import { useRef, useState } from 'react';
import { Maximize2 } from 'lucide-react';
import { launchApp } from './api';

import { useVoice } from './hooks/useVoice';
import { useChat } from './hooks/useChat';
import { usePlanner } from './hooks/usePlanner';
import { useOvernight } from './hooks/useOvernight';
import { useSkills } from './hooks/useSkills';
import { useBuzzcaf } from './hooks/useBuzzcaf';
import { useFileSearch } from './hooks/useFileSearch';
import { useServerEvents } from './hooks/useServerEvents';
import { useOnlineStatus } from './hooks/useOnlineStatus';

import { Header } from './components/Header';
import { ReminderBanners } from './components/ReminderBanners';
import { TaskTray } from './components/TaskTray';
import { VoiceVisualizer } from './components/VoiceVisualizer';
import { ChatFeed } from './components/ChatFeed';
import { DayPlanner } from './components/DayPlanner';
import { FileExplorer } from './components/FileExplorer';
import { BuzzcafPanel } from './components/BuzzcafPanel';
import { OvernightQueue } from './components/OvernightQueue';
import { AppLauncher } from './components/AppLauncher';
import { SkillsPanel } from './components/SkillsPanel';

// App is now composition + layout only. Every unit of behaviour lives in a hook
// (src/hooks) and every unit of UI in a component (src/components); this file
// wires them together (roadmap Phase 6, item 1).
//
// Two shared refs break the natural voice<->chat cycle without merging the two
// back into one object: the mic (registered once, in useVoice) reads the current
// thinking flag and dispatches recognised commands through them, while chat owns
// and updates both.
export default function App() {
  const [isExpanded, setIsExpanded] = useState(false);

  const isThinkingRef = useRef(false);
  const commandHandlerRef = useRef<(text: string) => void>(() => {});
  // Buzzcaf/app-launch narration is pushed into chat; a ref lets those hooks
  // reach the chat sink even though chat is created after them.
  const pushMessageRef = useRef<(text: string) => void>(() => {});

  const voice = useVoice({ isThinkingRef, commandHandlerRef });
  const planner = usePlanner();
  const overnight = useOvernight();
  const skills = useSkills();
  const buzzcaf = useBuzzcaf((text) => pushMessageRef.current(text));
  const fileSearch = useFileSearch();
  const online = useOnlineStatus();

  const chat = useChat({
    voice,
    isThinkingRef,
    commandHandlerRef,
    onTurnComplete: () => {
      planner.refresh();
      overnight.refresh();
      skills.refresh();
      buzzcaf.refresh();
    },
  });
  pushMessageRef.current = chat.pushDexterMessage;

  const events = useServerEvents({ voice, refreshPlanner: planner.refresh });

  const toggleExpandWindow = () => {
    const nextState = !isExpanded;
    setIsExpanded(nextState);
    const electronAPI = (window as any).electronAPI;
    if (electronAPI) {
      if (nextState) electronAPI.setExpanded();
      else electronAPI.setCompact();
    }
  };

  const handleLaunchApp = async (appName: string) => {
    try {
      await launchApp(appName);
      chat.pushDexterMessage(`Launched desktop application '${appName}'`);
    } catch (e) {
      console.warn('Could not launch app', e);
    }
  };

  return (
    <div
      style={{
        padding: '14px',
        maxWidth: isExpanded ? '1440px' : '460px',
        margin: '0 auto',
        transition: 'max-width 0.3s ease',
      }}
    >
      <ReminderBanners
        reminders={events.reminders}
        onSnooze={events.snoozeReminder}
        onDismiss={events.dismissReminder}
      />

      <TaskTray
        tasks={events.bgTasks}
        onAnswer={events.answerTask}
        onCancel={events.cancelBgTask}
        onDismiss={events.dismissBgTask}
      />

      <Header
        isVoiceMuted={voice.isVoiceMuted}
        onToggleMute={voice.toggleVoiceMute}
        isExpanded={isExpanded}
        onToggleExpand={toggleExpandWindow}
        online={online}
      />

      {/* Main grid: compact by default, three columns when expanded. */}
      <div style={{ display: 'grid', gridTemplateColumns: isExpanded ? '340px 1fr 340px' : '1fr', gap: '16px' }}>
        {/* Synaptic visualizer + continuous voice workspace */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: '14px' }}>
          <VoiceVisualizer
            isSpeaking={voice.isSpeaking}
            isThinking={chat.isThinking}
            isVoiceMuted={voice.isVoiceMuted}
            isListening={voice.isListening}
          />

          <ChatFeed
            chatLog={chat.chatLog}
            isThinking={chat.isThinking}
            toolActivity={chat.toolActivity}
            inputMsg={chat.inputMsg}
            setInputMsg={chat.setInputMsg}
            onSend={() => chat.handleSendMessage()}
            isExpanded={isExpanded}
            isVoiceMuted={voice.isVoiceMuted}
          />

          {!isExpanded && (
            <div style={{ display: 'flex', justifyContent: 'space-between', gap: '8px' }}>
              <button
                type="button"
                onClick={toggleExpandWindow}
                style={{
                  flex: 1,
                  background: 'rgba(15, 23, 42, 0.6)',
                  color: '#38bdf8',
                  border: '1px solid var(--bg-card-border)',
                  padding: '8px',
                  borderRadius: '10px',
                  fontSize: '11px',
                  fontWeight: 700,
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  gap: '6px',
                }}
              >
                <Maximize2 size={12} /> Open Full Day Planner & Curation Dashboard
              </button>
            </div>
          )}
        </div>

        {/* Expanded middle column: day planner + local file explorer */}
        {isExpanded && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: '16px' }}>
            <DayPlanner
              tasks={planner.tasks}
              newTaskTitle={planner.newTaskTitle}
              setNewTaskTitle={planner.setNewTaskTitle}
              onAdd={planner.addTask}
              onToggle={planner.toggleTask}
              isAdding={planner.isAdding}
            />
            <FileExplorer
              query={fileSearch.query}
              setQuery={fileSearch.setQuery}
              results={fileSearch.results}
              isSearching={fileSearch.isSearching}
              onSearch={fileSearch.search}
              onOpen={fileSearch.openFile}
            />
          </div>
        )}

        {/* Expanded right column: BuzzcafAI, overnight queue, launcher, skills */}
        {isExpanded && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: '16px' }}>
            <BuzzcafPanel
              projects={buzzcaf.projects}
              online={buzzcaf.online}
              isExecutingStep={buzzcaf.isExecutingStep}
              isStarting={buzzcaf.isStarting}
              onExecute={buzzcaf.executeStep}
              onStart={buzzcaf.start}
            />
            <OvernightQueue items={overnight.items} isCurating={overnight.isCurating} onCurate={overnight.curate} />
            <AppLauncher onLaunch={handleLaunchApp} />
            <SkillsPanel skills={skills.skills} />
          </div>
        )}
      </div>
    </div>
  );
}
