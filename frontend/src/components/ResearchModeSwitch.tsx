import { translate, type Locale } from "../i18n/core";
import { useI18n } from "../i18n/context";
import type { components } from "../api/generated/schema";
import "./research-mode-switch.css";
export type ResearchMode = components["schemas"]["ResearchDisplayMode"];
const OPTIONS = (locale: Locale): readonly {
    mode: ResearchMode;
    label: string;
    description: string;
}[] => ([
    {
        mode: "research",
        label: translate(locale, "common.shared.verifiedResearch"),
        description: translate(locale, "common.shared.rulesAndModelTasksThatPassedTheGates"),
    },
    {
        mode: "challenger",
        label: translate(locale, "common.shared.experimentalCandidate"),
        description: translate(locale, "common.shared.includesCandidateOutputsThatFailedGates"),
    },
    {
        mode: "formal",
        label: translate(locale, "common.shared.formalForecasts"),
        description: translate(locale, "common.shared.completeChampionModelOnly"),
    },
]);
interface ResearchModeSwitchProps {
    formalAvailable: boolean;
    mode: ResearchMode;
    onChange: (mode: ResearchMode) => void;
}
export function ResearchModeSwitch({ formalAvailable, mode, onChange, }: ResearchModeSwitchProps) {
    const { locale } = useI18n();
    return (<div aria-label={translate(locale, "common.shared.researchDisplayMode")} className="research-mode-switch" role="group">
      {OPTIONS(locale).map((option) => {
            const awaitingChampion = option.mode === "formal" && !formalAvailable;
            return (<button aria-pressed={mode === option.mode} className={awaitingChampion ? "is-awaiting-champion" : undefined} key={option.mode} onClick={() => onChange(option.mode)} title={awaitingChampion
                    ? translate(locale, "common.shared.viewFormalForecastPublicationStatusFormalValuesRemain") : option.description} type="button">
            <strong>
              {option.label}
              {awaitingChampion ? (<span className="research-mode-switch__badge">{"" + (translate(locale, "common.shared.notPublished") + "")}</span>) : null}
            </strong>
            <span>{option.description}</span>
          </button>);
        })}
    </div>);
}
