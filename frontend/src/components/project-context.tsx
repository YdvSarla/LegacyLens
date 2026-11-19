"use client";

import { createContext, useContext } from "react";
import type { Project } from "@/lib/types";

export interface ProjectContextValue {
  project: Project;
  refresh: () => void;
}

export const ProjectContext = createContext<ProjectContextValue | null>(null);

export function useProject(): ProjectContextValue {
  const value = useContext(ProjectContext);
  if (!value) throw new Error("useProject must be used inside a project page.");
  return value;
}
