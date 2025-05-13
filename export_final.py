#!/usr/bin/env python
# coding: utf-8

# In[ ]:


# === SHARED LOGIC (e.g. negated labels, exports) =============
# === Extract Concept Roles from .pre.xml for mapping ===

# === Export to CSV
df_concept_roles.to_csv(filename_roles_export, index=False)
print(f"💾 Exported to: {filename_roles_export}")

# === Check Negated Labels ===

# Optional export to CSV
filename_negated_export = f"{CIK}_{annual_label}_negated_label_tags.csv"
df_negated_labels.to_csv(filename_negated_export, index=False)
print(f"💾 Exported to: {filename_negated_export}")

#Preview of the enrichment results for target current and prior year filing with export to review


# In[ ]:


# === 4Q WORKFLOW =============================================
# Enrich target 10-K's and prior 10-Q's data with labels (for 4Q calculations)

if FOUR_Q_MODE:

    # === Export current year Q3 facts ===
    curr_q3_path = f"{TICKER}_{target_label}_current_q3_facts.csv"
    df_q3.to_csv(curr_q3_path, index=False)
    print(f"✅ Exported: {curr_q3_path}")

    # === Export prior year Q3 facts ===
    prior_q3_path = f"{TICKER}_{target_label}_prior_q3_facts.csv"
    df_q3_prior.to_csv(prior_q3_path, index=False)
    print(f"✅ Exported: {prior_q3_path}")

    # === Export prior year 10-K facts ===
    prior_10k_path = f"{TICKER}_{target_label}_prior_10k_facts.csv"
    df_prior_10k.to_csv(prior_10k_path, index=False)
    print(f"✅ Exported: {prior_10k_path}") 


# In[ ]:


# === SHARED LOGIC (Enrichment summary) =============
#Preview of the enrichment results for target current and prior year filing with export to review

if FOUR_Q_MODE:

    # Export results_df to CSV
    output_path = f"{TICKER}_{annual_label}_extracted_facts_full.csv"
    df_current_10k.to_csv(output_path, index=False)
    print(f"✅ Exported to {output_path}")

else:

    # Export results_df to CSV
    output_path = f"{TICKER}_{target_label}_extracted_facts_full.csv"
    df_current.to_csv(output_path, index=False)
    print(f"✅ Exported to {output_path}")

    prior_output_path = f"{TICKER}_{target_label}_prior_extracted_facts_full.csv"
    df_prior.to_csv(prior_output_path, index=False)
    print(f"✅ Exported to {prior_output_path}")


# In[ ]:


# === DEBUG: Enrichment Value Counts (Optional Block) =============
# Summary of how facts from current and prior filings are categorized
# Sanity check enrichment logic is working correctly

if DEBUG_MODE:
    
    if FOUR_Q_MODE:
        print("\n🔎 df_current_10k matched_category distribution:")
        print(df_current_10k['matched_category'].value_counts(dropna=False))
    
        print("\n🔎 df_prior_10k matched_category distribution:")
        print(df_prior_10k['matched_category'].value_counts(dropna=False))
    
    else:
        print("\n🔎 df_current (10-Q) matched_category distribution:")
        print(df_current['matched_category'].value_counts(dropna=False))
    
        print("\n🔎 df_prior (prior 10-Q) matched_category distribution:")
        print(df_prior['matched_category'].value_counts(dropna=False))


# In[ ]:


# === 4Q WORKFLOW =============================================
# === MATCH FY: Current FY vs Prior FY using tag + date type from current 10-K ===
if FOUR_Q_MODE:

    # === Export full FY match dataframe to CSV
    export_path_fy_all = f"{TICKER}_{target_label}_FY_matched_full.csv"
    df_fy_matched.to_csv(export_path_fy_all, index=False)
    print(f"📄 Exported full FY matched data to: {export_path_fy_all}")

# === MATCH YTD (from same Q3 10-Q in the fiscal year): current_ytd vs prior_ytd ===

    # === Export full YTD match dataframe to CSV
    export_path_ytd_all = f"{TICKER}_{target_label}_YTD_matched_full.csv"
    df_ytd_matched.to_csv(export_path_ytd_all, index=False)
    print(f"📄 Exported full YTD matched data to: {export_path_ytd_all}")

# === CALCULATE 4Q: From Matched FY and YTD using aligned suffixes ===
    
    # === Export for review
    output_path = f"{TICKER}_{target_label}_4Q_calculated_output.csv"
    df_4q_output.to_csv(output_path, index=False)
    print(f"📄 Exported to: {output_path}")

# === IDENTIFY UNMATCHED ROWS AFTER EXACT MERGE ===

    # === Export unmatched FY and YTD rows for review
    unmatched_fy_path = f"{TICKER}_{target_label}_4Q_unmatched_fy.csv"
    unmatched_ytd_path = f"{TICKER}_{target_label}_4Q_unmatched_ytd.csv"

    df_fy_unmatched.to_csv(unmatched_fy_path, index=False)
    df_ytd_unmatched.to_csv(unmatched_ytd_path, index=False)

    print(f"📄 Exported unmatched FY rows to: {unmatched_fy_path}")
    print(f"📄 Exported unmatched YTD rows to: {unmatched_ytd_path}")

# === FUZZY MATCH UNMATCHED ROWS ACROSS AXIS COLUMNS ===

    # === Export fuzzy-matched results to CSV for inspection
    fuzzy_output_path = f"{TICKER}_{target_label}_4Q_fuzzy_fallback_matches.csv"
    df_fuzzy_merged.to_csv(fuzzy_output_path, index=False)
    print(f"📄 Exported fuzzy fallback matches to: {fuzzy_output_path}")

# === COMBINE exact and fuzzy matches and finalize 4Q output

    # Step 3: Export full raw merged for audit
    raw_path = f"{TICKER}_{target_label}_4Q_full_merged_with_4Q_values.csv"
    df_merged.to_csv(raw_path, index=False)
    print(f"📄 Exported raw merged 4Q values to: {raw_path}")

    # Step 5: Export clean version
    clean_path = f"{TICKER}_{target_label}_4Q_final_output_cleaned.csv"
    df_4q_output.to_csv(clean_path, index=False)
    print(f"✅ Final standardized 4Q output exported to: {clean_path}")

# === Audit Fuzzy Matches ===

    # === Export borderline audit to CSV
    borderline_output_path = f"{TICKER}_{target_label}_fuzzy_near_misses.csv"
    df_borderline_audit.to_csv(borderline_output_path, index=False)
    print(f"📄 Exported borderline fuzzy matches to: {borderline_output_path}")

# === Match Instants: Current Q vs Prior Q from 10-K ===

    # === Export
    output_filename = f"{TICKER}_{target_label}_4Q_instants_current_vs_prior.csv"
    df_instants.to_csv(output_filename, index=False)
    print(f"📄 Exported to: {output_filename}")

# === FINALIZE 4Q COMBINED OUTPUT ==============================

    # === EXPORT ===================================================
    
    export_filename = f"{TICKER}_{target_label}_4Q_combined_final_output.csv"
    df_final_combined.to_csv(export_filename, index=False)
    print(f"📄 Exported to: {export_filename}")


# In[ ]:


# === 4Q WORKFLOW =============================================
# === Audit: Check what tags in the 10-K were missed in final export ===

if FOUR_Q_MODE:
    
        # Optional: export to CSV
        summary.to_csv(f"{TICKER}_{target_label}_missing_tags_summary.csv", index=False)
        df_missing.to_csv(f"{TICKER}_{target_label}_missing_tags_full.csv", index=False)
        print(f"📄 Exported full tag list to: {TICKER}_{target_label}_missing_tags_full.csv")
        # print(f"📄 Exported summary to: {TICKER}_{target_label}_missing_tags_summary.csv") # Can export summary if wanted
    


# In[ ]:


# === FULL YEAR WORKFLOW =============================================
# === Match Full Year (current vs prior) from 10-K ===

if FOUR_Q_MODE and FULL_YEAR_MODE:
    
    # === Export
    output_filename = f"{TICKER}_{target_label}_4Q_full_year_matched.csv"
    df_full_year_matched.to_csv(output_filename, index=False)
    print(f"📄 Exported to: {output_filename}")

# === Combine FY flow and instant facts for export ============

    # === Export
    filename_fy = f"{TICKER}_{annual_label}_final_output.csv"
    df_final_fy.to_csv(filename_fy, index=False)
    print(f"📄 Exported to: {filename_fy}")


# In[ ]:


# === NORMAL 10-Q WORKFLOW ====================================
# === Structured match: Match Current Quarter vs Prior Quarter in the Current Target 10Q ===

if not FOUR_Q_MODE:
    # === Normal 10-Q build
    
    # Build dynamic filename
    quarter_label = target_label  # Example: "2Q24"
    cik_str = CIK if isinstance(CIK, str) else str(CIK)
    filename = f"{TICKER}_{quarter_label}_filing_only_facts.csv"

    # Save to file
    df_final.to_csv(filename, index=False)
    print(f"\n✅ Exported to: {filename}")

# === Structured match: YTD and Instant facts using the Prior Year's Quarterly Filing ===

    # === Build dynamic filename
    quarter_label = target_label  # Example: "2Q24"
    cik_str = CIK if isinstance(CIK, str) else str(CIK)
    
    filename = f"{TICKER}_{quarter_label}_facts.csv"
    
    # === Save to file
    df_final.to_csv(filename, index=False)
    print(f"\n✅ Exported to: {filename}")

# === Deeper Audit: Check if any tags in the current 10-Q were missed  ===

    # === Output ===
    if missing_tags:
        
        # Construct filename: e.g., MSCI_1Q23_missing_tags.csv
        missing_tag_filename = f"{TICKER}_{target_label}_missing_tags.csv"
    
        # Export to file
        df_missing_tags.to_csv(missing_tag_filename, index=False)
        print(f"📝 Exported missing tag list to: {missing_tag_filename}")

# === Select facts in df_current where tag is in missing tags to check values

    # === Export to CSV
    df_missing_facts.to_csv(f"missing_tags_facts_{target_label}.csv", index=False)
    print(f"\n📄 Exported to missing_tags_facts_{target_label}.csv")

# === Fallback Match: Unmatched tags (YTD + Instant) with Current / Prior 10-Q ====================================

    # Export
    fallback_filename = f"{TICKER}_{target_label}_fallback_matches.csv"
    df_fallback_clean.to_csv(fallback_filename, index=False)
    print(f"📄 Exported clean fallback output: {fallback_filename}")
    display(df_fallback_clean.head())

# === Collision Audit for Fallback Matches ===

    if not flagged_fallback_df.empty:
        fallback_collision_filename = f"{TICKER}_{target_label}_fallback_collision_flags.csv"
        flagged_fallback_df[["tag", "current_period_value", "prior_period_value"]].to_csv(
            fallback_collision_filename, index=False
        )
        print(f"📄 Fallback collision audit exported to: {fallback_collision_filename}")
    else:
        print("✅ No collision flags in fallback output — no file exported.")

# === Audit to check for duplicate prior year values in fallback and final output ====================================

    if not overlap.empty:
        # Export to CSV for manual review
        overlap_filename = f"{TICKER}_{target_label}_fallback_overlap_review.csv"
        overlap.to_csv(overlap_filename, index=False)
        print(f"📄 Exported overlap review file: {overlap_filename}")

    else:
        print("✅ No overlapping prior values found.")

# === Finalize fallback matches by removing overlapping prior values ============================
    
    # Step 3: Export to CSV for review
    unique_fallback_filename = f"{TICKER}_{target_label}_fallback_unique.csv"
    df_fallback_unique.to_csv(unique_fallback_filename, index=False)
    print(f"📄 Exported unique fallback matches to: {unique_fallback_filename}")

# === Append fallback matches to df_final before visual export ================
    
    # === Optional: Export combined df_final to CSV for review before sign flipping
    combined_export_filename = f"{TICKER}_{target_label}_final_combined_before_visual.csv"
    df_final.to_csv(combined_export_filename, index=False)
    print(f"📄 Exported full df_final before sign logic to: {combined_export_filename}")

# === Audit: New disclosures (tag + axis) not seen in prior year's Q ===

    # Step 5: Export
    new_filename = f"{TICKER}_{target_label}_new_disclosures.csv"
    df_new_disclosures.to_csv(new_filename, index=False)
    print(f"📄 Exported to: {new_filename}")

# === Identify Facts (currentQ and YTD) That Did Not Make Final Output ===

    # Optional: Export to CSV for audit
    filename_new_disclosures = f"{TICKER}_{target_label}_new_disclosures.csv"
    filename_summary = f"{TICKER}_{target_label}_new_disclosures_summary.csv"
    
    df_new_disclosures.to_csv(filename_new_disclosures, index=False)
    print(f"📄 Exported new disclosures to: {filename_new_disclosures}")

    summary.to_csv(filename_summary, index=False)
    print(f"📄 Exported summary: {filename_summary}")


# In[ ]:


# === Export flagged collision audit ===
if not flagged_df.empty:
    collision_label = annual_label if FULL_YEAR_MODE else target_label
    collision_filename = f"{TICKER}_{target_label}_collision_flags.csv"
    flagged_df[["tag", "current_period_value", "prior_period_value"]].to_csv(collision_filename, index=False)
    print(f"📄 Collision audit exported to: {collision_filename}")

else:
    print("✅ No collision flags found — no file exported.")


# In[ ]:


# === SHARED LOGIC (Apply Visual Logic and Export Dataframe) =============
# === Create New DataFrame with Visual Values Using Company Negate Labels ===

# === Export CSV (only visual values)
export_df[["tag", "visual_current_value", "visual_prior_value", "presentation_role", "collision_flag"]].to_csv(filename, index=False)
print(f"✅ Exported to: {filename}")

