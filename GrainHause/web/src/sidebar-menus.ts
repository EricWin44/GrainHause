import {
  Aperture,
  Bookmark,
  Crop,
  Download,
  Fingerprint,
  Frame,
  Lightbulb,
  Palette,
  Smile,
  Sparkles,
  Spline,
  Sun,
  Thermometer,
  Zap,
  type LucideIcon,
} from "lucide-react";

export type SidebarMenuItem = {
  id: string;
  label: string;
  icon: LucideIcon;
};

export type SidebarMenuGroup = {
  label: string;
  items: SidebarMenuItem[];
};

/** Sidebar destinations. Each id matches a develop-panel section. */
export const SIDEBAR_MENUS: SidebarMenuGroup[] = [
  {
    label: "Develop",
    items: [
      { id: "film-stock", label: "Film stock", icon: Aperture },
      { id: "exposure", label: "Exposure", icon: Sun },
      { id: "white-balance", label: "White balance", icon: Thermometer },
      { id: "colour-lab", label: "Colour & lab", icon: Palette },
      { id: "skin-tones", label: "Skin tones", icon: Smile },
      { id: "tone-curve", label: "Tone curve", icon: Spline },
      { id: "crop", label: "Crop", icon: Crop },
      { id: "grain", label: "Grain", icon: Sparkles },
      { id: "light", label: "Light", icon: Lightbulb },
      { id: "light-leaks", label: "Light leaks", icon: Zap },
      { id: "dust", label: "Dust & texture", icon: Fingerprint },
      { id: "frame", label: "Frame & border", icon: Frame },
    ],
  },
  {
    label: "Finish",
    items: [
      { id: "looks", label: "Your looks", icon: Bookmark },
      { id: "export", label: "Export", icon: Download },
    ],
  },
];
