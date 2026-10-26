interface SwitchProps {
  checked: boolean;
  onChange: (next: boolean) => void;
  /** Id of the element that names the switch. */
  labelledBy: string;
  describedBy?: string;
}

/** Toggle. The write is immediate: a tap is durable when the finger leaves the screen. */
export function Switch({ checked, onChange, labelledBy, describedBy }: SwitchProps) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-labelledby={labelledBy}
      aria-describedby={describedBy}
      className="kk-switch"
      onClick={() => onChange(!checked)}
    >
      <span className="kk-switch-track">
        <span className="kk-switch-knob" />
      </span>
    </button>
  );
}
