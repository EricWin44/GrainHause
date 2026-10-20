import type { ComponentProps } from "react";
import { Select } from "@base-ui/react/select";

export type SelectOption<T extends string> = { value: T; label: string };

/** Dropdown used by every develop-panel menu. Same structure as the select-1 example, in the app's dark colours. */
export function SelectMenu<T extends string>({
  value,
  options,
  onChange,
  disabled,
  title,
}: {
  value: T;
  options: SelectOption<T>[];
  onChange: (value: T) => void;
  disabled?: boolean;
  title?: string;
}) {
  return (
    <Select.Root
      items={options}
      value={value}
      disabled={disabled}
      onValueChange={(next) => {
        if (next != null) onChange(next);
      }}
    >
      <Select.Trigger
        title={title}
        onKeyDown={(e) => e.stopPropagation()}
        className="gh-select flex h-8 min-w-0 flex-1 items-center justify-between gap-2 rounded-md border border-[#2a2b30] bg-[#141417] pr-2.5 pl-2.5 text-left text-[13px] text-[#e8e9ec] select-none hover:bg-[#18181c] focus-visible:outline focus-visible:outline-2 focus-visible:-outline-offset-1 focus-visible:outline-[#9ea3ab] data-[popup-open]:bg-[#18181c] disabled:cursor-not-allowed disabled:opacity-45"
      >
        <Select.Value className="truncate" />
        <Select.Icon className="flex shrink-0 text-[#8d929b]">
          <ChevronUpDownIcon />
        </Select.Icon>
      </Select.Trigger>
      <Select.Portal>
        <Select.Positioner className="z-50 outline-none" sideOffset={8}>
          <Select.ScrollUpArrow className="top-0 z-[1] flex h-4 w-full cursor-default items-center justify-center rounded-md bg-[#18181c] text-center text-xs text-[#8d929b] before:absolute before:top-[-100%] before:left-0 before:h-full before:w-full before:content-[''] data-[direction=down]:bottom-0 data-[direction=down]:before:bottom-[-100%]" />
          <Select.Popup
            onKeyDown={(e) => e.stopPropagation()}
            className="group z-50 max-h-[var(--available-height)] origin-[var(--transform-origin)] overflow-y-auto rounded-md bg-[#18181c] py-1 text-[#e8e9ec] shadow-lg shadow-black/50 outline outline-1 outline-[#2a2b30] transition-[transform,scale,opacity] data-[ending-style]:scale-90 data-[ending-style]:opacity-0 data-[ending-style]:transition-none data-[starting-style]:scale-90 data-[starting-style]:opacity-0 data-[side=none]:data-[starting-style]:scale-100 data-[side=none]:data-[starting-style]:opacity-100 data-[side=none]:data-[starting-style]:transition-none"
          >
            {options.map(({ label, value: itemValue }) => (
              <Select.Item
                key={itemValue}
                value={itemValue}
                className="grid min-w-[var(--anchor-width)] cursor-default grid-cols-[0.75rem_1fr] items-center gap-2 py-1.5 pr-4 pl-2.5 text-sm leading-4 outline-none select-none group-data-[side=none]:min-w-[calc(var(--anchor-width)+1rem)] group-data-[side=none]:pr-12 data-[highlighted]:relative data-[highlighted]:z-0 data-[highlighted]:text-[#0b0b0c] data-[highlighted]:before:absolute data-[highlighted]:before:inset-x-1 data-[highlighted]:before:inset-y-0 data-[highlighted]:before:z-[-1] data-[highlighted]:before:rounded-sm data-[highlighted]:before:bg-[#e4e7ec]"
              >
                <Select.ItemIndicator className="col-start-1">
                  <CheckIcon className="size-3" />
                </Select.ItemIndicator>
                <Select.ItemText className="col-start-2">{label}</Select.ItemText>
              </Select.Item>
            ))}
          </Select.Popup>
          <Select.ScrollDownArrow className="bottom-0 z-[1] flex h-4 w-full cursor-default items-center justify-center rounded-md bg-[#18181c] text-center text-xs text-[#8d929b] before:absolute before:top-[-100%] before:left-0 before:h-full before:w-full before:content-[''] data-[direction=down]:bottom-0 data-[direction=down]:before:bottom-[-100%]" />
        </Select.Positioner>
      </Select.Portal>
    </Select.Root>
  );
}

function ChevronUpDownIcon(props: ComponentProps<"svg">) {
  return (
    <svg width="8" height="12" viewBox="0 0 8 12" fill="none" stroke="currentcolor" strokeWidth="1.5" {...props}>
      <path d="M0.5 4.5L4 1.5L7.5 4.5" />
      <path d="M0.5 7.5L4 10.5L7.5 7.5" />
    </svg>
  );
}

function CheckIcon(props: ComponentProps<"svg">) {
  return (
    <svg fill="currentcolor" width="10" height="10" viewBox="0 0 10 10" {...props}>
      <path d="M9.1603 1.12218C9.50684 1.34873 9.60427 1.81354 9.37792 2.16038L5.13603 8.66012C5.01614 8.8438 4.82192 8.96576 4.60451 8.99384C4.3871 9.02194 4.1683 8.95335 4.00574 8.80615L1.24664 6.30769C0.939709 6.02975 0.916013 5.55541 1.19372 5.24822C1.47142 4.94102 1.94536 4.91731 2.2523 5.19524L4.36085 7.10461L8.12299 1.33999C8.34934 0.993152 8.81376 0.895638 9.1603 1.12218Z" />
    </svg>
  );
}
