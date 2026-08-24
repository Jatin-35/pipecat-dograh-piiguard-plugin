# Frontend Changes Required

Exact edits needed in `dograh-hq/dograh`'s `ui/` to add a per-workflow
enable/disable toggle for PII redaction, in the workflow Settings page — the
same place `voicemail_detection` lives, built to match its exact pattern so
it looks and behaves like a feature Dograh shipped natively, not a bolted-on
extra.

These edits do nothing on their own without `BACKEND_CHANGES.md` also
applied — the toggle writes `workflow_configurations.pii_redaction`, which
only has an effect once the backend reads it (§3b/§3c of that doc).

Both files below were verified against the real codebase: `npx tsc --noEmit`
passes with **zero errors** across the entire Next.js app after these edits,
and ESLint is clean.

## 1. `ui/src/types/workflow-configurations.ts`

Add the config type, its default, and register it on `WorkflowConfigurations` —
mirroring `VoicemailDetectionConfiguration` exactly:

```diff
 export const DEFAULT_VOICEMAIL_DETECTION_CONFIGURATION: VoicemailDetectionConfiguration = {
     enabled: false,
     use_workflow_llm: true,
     long_speech_timeout: 8.0,
 };

+export type PiiRedactionStrategy = "placeholder" | "hash" | "mask" | "redact";
+
+export interface PiiRedactionConfiguration {
+    enabled: boolean;
+    strategy: PiiRedactionStrategy;
+}
+
+export const DEFAULT_PII_REDACTION_CONFIGURATION: PiiRedactionConfiguration = {
+    enabled: false,
+    strategy: "placeholder",
+};
+
 export interface TranscriptConfiguration {
     include_end_timestamps: boolean;
 }
```

And in the `WorkflowConfigurations` type itself:

```diff
     voicemail_detection?: VoicemailDetectionConfiguration;
+    pii_redaction?: PiiRedactionConfiguration;
     transcript_configuration: TranscriptConfiguration;
```

That's the whole file. No changes to `resolveWorkflowConfigurations()` —
`voicemail_detection` isn't centrally resolved there either (it's absent from
that function's merge logic); both are resolved locally inside their own
settings-page section component instead, which is the next step.

## 2. `ui/src/app/workflow/[workflowId]/settings/page.tsx`

Four small edits to one file, all in the same style as the existing
`VoicemailSection` (search for that name to see the pattern this mirrors).

### 2a. Import the new icon and types

```diff
-import { ArrowLeft, BookA, Brain, CalendarIcon, Clipboard, Download, ExternalLink, FileDown, Fingerprint, Loader2, Mic, Pause, PhoneOff, Play, Plus, Rocket, Settings, Trash2Icon, Upload, Variable, X } from "lucide-react";
+import { ArrowLeft, BookA, Brain, CalendarIcon, Clipboard, Download, ExternalLink, FileDown, Fingerprint, Loader2, Mic, Pause, PhoneOff, Play, Plus, Rocket, Settings, ShieldAlert, Trash2Icon, Upload, Variable, X } from "lucide-react";
```

```diff
 import {
     type AmbientNoiseConfiguration,
+    DEFAULT_PII_REDACTION_CONFIGURATION,
     DEFAULT_PROVISIONAL_VAD_PAUSE_SECS,
     DEFAULT_TURN_START_MIN_WORDS,
     DEFAULT_VOICEMAIL_DETECTION_CONFIGURATION,
     type ExternalPBXFieldMapping,
+    type PiiRedactionConfiguration,
+    type PiiRedactionStrategy,
     resolveWorkflowConfigurations,
     ...
 } from "@/types/workflow-configurations";
```

### 2b. Register it in the settings sidebar nav

```diff
     { id: "voicemail", label: "Voicemail Detection", icon: PhoneOff },
+    { id: "pii-redaction", label: "PII Redaction", icon: ShieldAlert },
     { id: "recordings", label: "Recordings", icon: Mic },
```

### 2c. Add the section component

Insert this right after the `VoicemailSection` function definition ends
(search for the closing `}` right before the `// Section: Agent UUID`
comment block):

```tsx
// ---------------------------------------------------------------------------
// Section: PII Redaction
// ---------------------------------------------------------------------------

const PII_REDACTION_STRATEGY_OPTIONS: Array<{
    value: PiiRedactionStrategy;
    label: string;
    description: string;
}> = [
    {
        value: "placeholder",
        label: "Placeholder",
        description: "Replace with a tag like [EMAIL_ADDRESS]. Preserves conversational context.",
    },
    {
        value: "mask",
        label: "Mask",
        description: "Replace with asterisks (****1234), keeping the format recognizable.",
    },
    {
        value: "hash",
        label: "Hash",
        description: "Replace with a deterministic hash. Same value always redacts the same way.",
    },
    {
        value: "redact",
        label: "Redact",
        description: "Remove the value entirely, with no replacement text.",
    },
];

function PiiRedactionSection({
    workflowConfigurations,
    workflowName,
    onSave,
}: {
    workflowConfigurations: WorkflowConfigurations;
    workflowName: string;
    onSave: (configurations: WorkflowConfigurations, workflowName: string) => Promise<void>;
}) {
    const getConfig = (): PiiRedactionConfiguration => ({
        ...DEFAULT_PII_REDACTION_CONFIGURATION,
        ...workflowConfigurations.pii_redaction,
    });

    const [enabled, setEnabled] = useState(getConfig().enabled);
    const [strategy, setStrategy] = useState(getConfig().strategy);
    const [isSaving, setIsSaving] = useState(false);

    const isDirty = useMemo(() => {
        const init = {
            ...DEFAULT_PII_REDACTION_CONFIGURATION,
            ...workflowConfigurations.pii_redaction,
        };
        return enabled !== init.enabled || strategy !== init.strategy;
    }, [enabled, strategy, workflowConfigurations]);

    useUnsavedChanges("pii-redaction", isDirty);

    const handleSave = async () => {
        setIsSaving(true);
        try {
            const piiRedactionConfig: PiiRedactionConfiguration = {
                enabled,
                strategy,
            };
            await onSave(
                { ...workflowConfigurations, pii_redaction: piiRedactionConfig },
                workflowName,
            );
            toast.success(`PII redaction settings saved. ${PUBLISH_WORKFLOW_REMINDER}`);
        } catch (error) {
            console.error("Failed to save PII redaction settings:", error);
        } finally {
            setIsSaving(false);
        }
    };

    return (
        <Card id="pii-redaction">
            <CardHeader>
                <CardTitle className="flex items-center gap-2 text-base">
                    <ShieldAlert className="h-4 w-4" />
                    PII Redaction
                </CardTitle>
                <CardDescription>
                    Detect and redact sensitive information (emails, phone numbers, card
                    numbers, and more) before it reaches the LLM and before it&apos;s stored
                    in the call transcript.
                </CardDescription>
            </CardHeader>
            <CardContent className="space-y-4">
                <div className="flex items-center space-x-2 rounded-md border bg-muted/20 p-2">
                    <Switch id="pii-redaction-enabled" checked={enabled} onCheckedChange={setEnabled} />
                    <Label htmlFor="pii-redaction-enabled">Enable PII Redaction</Label>
                </div>

                {enabled && (
                    <div className="space-y-2">
                        <Label htmlFor="pii-redaction-strategy" className="text-xs">
                            Replacement Strategy
                        </Label>
                        <Select
                            value={strategy}
                            onValueChange={(value: PiiRedactionStrategy) => setStrategy(value)}
                        >
                            <SelectTrigger id="pii-redaction-strategy">
                                <SelectValue placeholder="Select strategy" />
                            </SelectTrigger>
                            <SelectContent>
                                {PII_REDACTION_STRATEGY_OPTIONS.map((option) => (
                                    <SelectItem key={option.value} value={option.value}>
                                        {option.label}
                                    </SelectItem>
                                ))}
                            </SelectContent>
                        </Select>
                        <p className="text-xs text-muted-foreground">
                            {PII_REDACTION_STRATEGY_OPTIONS.find((o) => o.value === strategy)?.description}
                        </p>
                    </div>
                )}

                {enabled && (
                    <p className="text-xs text-muted-foreground">
                        Not available for realtime speech-to-speech agents: those pipelines have
                        no text transcript to redact before the model receives audio.
                    </p>
                )}
            </CardContent>
            <CardFooter className="justify-end gap-3 border-t pt-6">
                {isDirty && <span className="text-xs text-muted-foreground">Unsaved changes</span>}
                <Button onClick={handleSave} disabled={isSaving || !isDirty}>
                    {isSaving ? "Saving..." : "Save PII Redaction Settings"}
                </Button>
            </CardFooter>
        </Card>
    );
}
```

### 2d. Render it in the settings page, next to Voicemail Detection

```diff
                             {/* Voicemail Detection */}
                             <VoicemailSection
                                 workflowConfigurations={resolvedWorkflowConfigurationsForRender}
                                 workflowName={workflowName}
                                 onSave={saveWorkflowConfigurations}
                             />

+                            {/* PII Redaction */}
+                            <PiiRedactionSection
+                                workflowConfigurations={resolvedWorkflowConfigurationsForRender}
+                                workflowName={workflowName}
+                                onSave={saveWorkflowConfigurations}
+                            />
+
                             {/* Recordings – moved to org-level page */}
```

## Why these edits are safe

Specifically checked, not assumed:

- **No hardcoded section whitelist to update elsewhere.** `useUnsavedChanges`
  (`context/UnsavedChangesContext.tsx`) has no fixed list of known section
  ids — sections register themselves dynamically, so `"pii-redaction"` needs
  no registration anywhere but the component itself.
- **The save round-trip preserves everything else.** `saveWorkflowConfigurations`
  (`hooks/useWorkflowState.ts`) sends the *entire* `workflow_configurations`
  object via `PUT`, not a partial diff — and `PiiRedactionSection.handleSave`
  spreads the full `workflowConfigurations` prop before overriding just
  `pii_redaction`, so no other section's settings (voicemail, model
  overrides, etc.) can be clobbered by saving this one.
- **The backend accepts the new field with zero schema changes.** The `PUT
  /{workflow_id}` route's `UpdateWorkflowRequest.workflow_configurations` is
  typed as `WorkflowConfigurationDefaults`, which the codebase's own comment
  confirms is `extra="allow"` specifically to keep passthrough keys intact —
  the same mechanism `voicemail_detection` already relies on.
- **Type-checked and linted against the real app, not in isolation** — `npx
  tsc --noEmit -p tsconfig.json` passes with zero errors across the whole
  Next.js project after these edits, and `npx eslint` on both changed files
  is clean.
