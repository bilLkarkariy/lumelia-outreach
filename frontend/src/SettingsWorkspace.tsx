import type {ReactNode} from 'react';
import {Bell, Coins, DatabaseBackup, Flame, History, Phone, PlugZap} from 'lucide-react';
import './settings.css';

export const settingsSections = [
  {id: 'connections', label: 'Connexions', icon: PlugZap},
  {id: 'warmup', label: 'Warm-up', icon: Flame},
  {id: 'phone', label: 'Téléphone', icon: Phone},
  {id: 'notifications', label: 'Notifications', icon: Bell},
  {id: 'budget', label: 'Budget', icon: Coins},
  {id: 'backup', label: 'Sauvegarde', icon: DatabaseBackup},
  {id: 'journal', label: 'Journal', icon: History},
];
export function SettingsWorkspace({
  current,
  onChange,
  children,
}: {
  current: string;
  onChange: (value: string) => void;
  children: ReactNode;
}) {
  return (
    <div className="settings-workspace">
      <div className="settings-sections" role="group" aria-label="Rubriques des réglages">
        {settingsSections.map(({id, label, icon: Icon}) => (
          <button key={id} type="button" aria-pressed={current === id} onClick={() => onChange(id)}>
            <Icon size={16} />
            {label}
          </button>
        ))}
      </div>
      {children}
    </div>
  );
}
