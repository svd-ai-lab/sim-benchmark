# Public task catalog

HWE-bench-CAE contains 68 public, runnable Harbor tasks across three tracks.
Every task uses Harbor schema 1.3 and includes an oracle solution.

## Summary

| track | tasks | solver |
|---|---:|---|
| battery | 34 | PyBaMM |
| cfd | 13 | OpenFOAM |
| combustion | 21 | Cantera |
| **total** | **68** | |

## Battery

### Cells

- `a123_lfp_discharge_1p15c_298k`
- `a123_lfp_energy_0p88c_300k`
- `a123_lfp_mean_voltage_1p52c_297k`
- `ecker_graphite_discharge_0p62c_293k`
- `ecker_graphite_energy_1p12c_298k`
- `ecker_graphite_mean_voltage_0p84c_292k`
- `kokam_lco_discharge_0p71c_302k`
- `lco_graphite_discharge_1p27c_295k`
- `lco_graphite_energy_1p73c_296k`
- `lgm50_fast_charge_current_limit_45c`
- `lgm50_nmc811_discharge_2p3c_288k`
- `lgm50_nmc811_energy_1p6c_301k`
- `lgm50_nmc811_mean_voltage_0p78c_294k`
- `nca_graphite_discharge_0p93c_299k`
- `nmc_pouch_discharge_1p8c_303k`
- `nmc_pouch_energy_2p4c_299k`

### Characterization

- `a123_cccv_charge_1p1c_from_soc0p26`
- `a123_pulse_resistance_1p8a_soc0p51`
- `kokam_cccv_charge_0p64c_from_soc0p31`
- `kokam_pulse_resistance_1p2a_soc0p47`
- `lco_graphite_pulse_resistance_0p9a_soc0p66`
- `lgm50_cccv_charge_1p52c_from_soc0p12`
- `lgm50_pulse_resistance_5a_soc0p62`
- `nca_cccv_charge_0p79c_from_soc0p35`
- `nca_pulse_resistance_0p6a_soc0p74`
- `nmc_pouch_cccv_charge_1p3c_from_soc0p23`
- `nmc_pouch_pulse_resistance_4p1a_soc0p55`

### Degradation

- `lgm50_plating_fade_9_cycles_1p3c`
- `lgm50_sei_fade_12_cycles_1c`
- `lgm50_sei_plating_fade_10_cycles_1p5c`

### Thermal

- `lco_graphite_thermal_rise_2p35c_299k`
- `lgm50_okane_thermal_rise_1p85c_292k`
- `lgm50_thermal_rise_0p91c_303k`
- `nmc_pouch_thermal_rise_2p7c_305k`

## CFD

- `backstep_laminar_armaly_re389`
- `bump_in_channel_2d`
- `channel_developing_entry`
- `channel_retau395_repair_closure`
- `cylinder_schafer_turek_2d1_cd`
- `de_vahl_davis_natural_convection_ra1e4`
- `flatplate_zpg_subsonic`
- `kovasznay_flow_re40`
- `lid_driven_cavity_ghia_re1000`
- `naca0012_subsonic`
- `plane_poiseuille_friction_factor`
- `taylor_green_vortex_2d_re100`
- `turbulent_channel_flow_retau590`

## Combustion

### Flames

- `c2h4_air_flame_speed_phi1p14_1p2atm_309k`
- `c2h6_air_flame_speed_phi1p09_1p3atm_316k`
- `c3h8_air_flame_speed_phi1p11_1p3atm_313k`
- `ch3oh_air_flame_speed_phi1p16_1p3atm_337k`
- `ch4_air_flame_speed_meet_resolution_spec`
- `ch4_air_flame_speed_phi1p24_1p6atm_306k`
- `h2_air_flame_speed_phi1p12_1p2atm_312k`
- `nh3_air_flame_speed_phi1p12_1p4atm_319k`
- `nh3_flame_speed_repair_mechanism`

### Kinetics

- `c2h2_air_idt_phi0p74_1462k_6p4atm`
- `c2h4_air_idt_phi0p68_1641k_11p8atm`
- `c2h6_air_idt_phi0p63_1547k_7p3atm`
- `c3h8_air_idt_phi0p59_1568k_8p1atm`
- `ch3oh_air_idt_phi0p66_1583k_6p8atm`
- `ch4_air_idt_phi0p55_1633k_9p2atm`
- `ch4_air_idt_resume_interrupted_run`
- `ch4_air_idt_sensitivity_ch3ho2_phi0p87_1390k`
- `h2_air_idt_phi0p57_1246k_5p4atm`
- `ndodecane_air_idt_phi0p83_1319k_14p2atm`
- `nh3_air_idt_phi0p72_1521k_5p1atm`
- `nhexane_air_idt_phi0p79_1442k_4p6atm`

Task metadata, KPI definitions and provenance live with each task under
`cases/<track>/<subdomain>/<case-id>/`.
