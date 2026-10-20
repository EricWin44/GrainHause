import { CircleHelp } from "lucide-react";

import { SIDEBAR_MENUS } from "@/sidebar-menus";
import {
  Sidebar,
  SidebarContent,
  SidebarFooter,
  SidebarGroup,
  SidebarGroupContent,
  SidebarGroupLabel,
  SidebarHeader,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
  SidebarRail,
} from "@/components/ui/sidebar";

export function AppSidebar({
  activeSection,
  onSelectSection,
  onHelp,
}: {
  activeSection: string;
  onSelectSection: (id: string) => void;
  onHelp: () => void;
}) {
  return (
    <Sidebar collapsible="icon">
      <SidebarHeader>
        <div className="flex items-center gap-2 px-1 py-1">
          <img src="/icon.png" alt="" width={28} height={28} className="size-7 shrink-0" />
          <span className="brand min-w-0 flex-1 truncate group-data-[collapsible=icon]:hidden">GRAINHAUSE</span>
        </div>
      </SidebarHeader>

      <SidebarContent>
        {SIDEBAR_MENUS.map((group) => (
          <SidebarGroup key={group.label}>
            <SidebarGroupLabel>{group.label}</SidebarGroupLabel>
            <SidebarGroupContent>
              <SidebarMenu>
                {group.items.map((item) => (
                  <SidebarMenuItem key={item.id}>
                    <SidebarMenuButton
                      isActive={activeSection === item.id}
                      tooltip={item.label}
                      onClick={() => onSelectSection(item.id)}
                    >
                      <item.icon />
                      <span>{item.label}</span>
                    </SidebarMenuButton>
                  </SidebarMenuItem>
                ))}
              </SidebarMenu>
            </SidebarGroupContent>
          </SidebarGroup>
        ))}
      </SidebarContent>

      <SidebarFooter>
        <SidebarMenu>
          <SidebarMenuItem>
            <SidebarMenuButton tooltip="Help & shortcuts" onClick={onHelp}>
              <CircleHelp />
              <span>Help</span>
            </SidebarMenuButton>
          </SidebarMenuItem>
        </SidebarMenu>
      </SidebarFooter>
      <SidebarRail />
    </Sidebar>
  );
}
