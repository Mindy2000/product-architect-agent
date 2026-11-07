import type { KnowledgeSource } from './KnowledgeLibrary'
import type { ExecutionTask } from './TaskPanel'
export type TraceEvent = {
  stage: string
  status: string
  summary: string
  estimated_cost_cny?: number
  input_tokens?: number
  output_tokens?: number
  at?: string
}
export type RunRecord = {
  local_data_restored?: boolean
  source_import?: boolean
  operator_assisted?: boolean
  operator_note?: string
  knowledge_sources?: KnowledgeSource[]
  runtime_version?: number
  executor_tasks?: ExecutionTask[]
  executor_turns?: number
  execution_step_limit?: number
  executor_feedback?: string
  execution_mode?: string
  run_id: string
  acceptance_criteria: string[]
  plan: string[]
  stage: string
  status: string
  repair_round: number
  parent_run_id?: string
  model_name?: string
  can_resume?: boolean
  user_request?: string
  created_at?: string
  product_spec?: {
    application_type?: 'frontend' | 'fullstack_crud'
    product_name: string
    summary: string
    target_users: string[]
    pages: string[]
    core_features: string[]
  }
  trace: TraceEvent[]
  build_status?: string
  test_status?: string
  build_output?: string
  test_output?: string
  last_error?: string
  artifact_paths?: Record<string, string>
}

export const terminalStates = new Set([
  'completed',
  'failed_validation',
  'failed',
  'failed_model',
  'failed_sandbox',
  'blocked_budget',
  'blocked_configuration',
  'interrupted',
  'paused',
  'cancelled',
])
