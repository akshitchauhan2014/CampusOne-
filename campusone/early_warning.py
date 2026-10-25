from __future__ import annotations
import hashlib, json, pickle
from datetime import datetime, timezone
from pathlib import Path
import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.preprocessing import StandardScaler, OneHotEncoder
from sklearn.impute import SimpleImputer
from sklearn.pipeline import Pipeline
from sklearn.base import clone
from sklearn.linear_model import LogisticRegression
from sklearn.ensemble import RandomForestClassifier
from sklearn.model_selection import train_test_split, StratifiedKFold, cross_validate
from sklearn.metrics import precision_score, recall_score, f1_score, roc_auc_score, confusion_matrix, accuracy_score
from .analytics import (
    APPROVED_FEATURES, TARGET, validate_feature_columns, rule_baseline,
    band_for_score, ACADEMIC_THRESHOLD,
)

SEED=42

def load_training_file(path):
    path=Path(path)
    if path.suffix.lower()==".csv":return pd.read_csv(path)
    if path.suffix.lower()==".xlsx":return pd.read_excel(path,engine="openpyxl")
    raise ValueError("Training data must be a .csv or .xlsx file.")

def _metrics(y_true,y_pred,proba):
    auc=None
    if len(set(y_true))==2:
        try:auc=float(roc_auc_score(y_true,proba))
        except ValueError:pass
    return {"precision":float(precision_score(y_true,y_pred,zero_division=0)),
            "recall":float(recall_score(y_true,y_pred,zero_division=0)),
            "f1":float(f1_score(y_true,y_pred,zero_division=0)),
            "roc_auc":auc,"accuracy":float(accuracy_score(y_true,y_pred)),
            "confusion_matrix":confusion_matrix(y_true,y_pred,labels=[0,1]).tolist()}

def train_and_evaluate(frame, output_dir, dataset_kind="historical", attendance_threshold=75.0,
                       comparison="random_forest"):
    if TARGET not in frame.columns:raise ValueError(f"Dataset must contain target column '{TARGET}'.")
    features=[c for c in frame.columns if c != TARGET]
    validate_feature_columns(features)
    academic_features=[name for name in ("internal_marks_pct","previous_result_pct") if name in features]
    if "attendance_pct" not in features or not academic_features:
        raise ValueError("Training data must include attendance_pct and at least one academic result percentage.")
    if set(frame.columns)&{"gender","religion","caste","phone","phone_number","parent_name","parents_name","family_income","income","fee","fees","fee_due","fee_paid","outstanding_fee"}:
        raise ValueError("Dataset includes prohibited sensitive, proxy, or fee fields.")
    data=frame[features+[TARGET]].dropna(subset=[TARGET]).copy()
    y=pd.to_numeric(data[TARGET],errors="raise").astype(int)
    if not set(y.unique()).issubset({0,1}) or y.nunique()!=2:raise ValueError("Target must contain both classes 0 and 1.")
    if len(data)<20 or y.value_counts().min()<5:raise ValueError("Insufficient data: need at least 20 rows and 5 examples of each target class.")
    X=data[features].copy()
    numeric=[c for c in features if pd.api.types.is_numeric_dtype(X[c])]
    categorical=[c for c in features if c not in numeric]
    transformers=[]
    if numeric:transformers.append(("num",Pipeline([("impute",SimpleImputer(strategy="median")),("scale",StandardScaler())]),numeric))
    if categorical:transformers.append(("cat",Pipeline([("impute",SimpleImputer(strategy="most_frequent")),("encode",OneHotEncoder(handle_unknown="ignore"))]),categorical))
    pre=ColumnTransformer(transformers)
    min_class=int(y.value_counts().min())
    test_size=max(0.2, min(0.35, 2*min_class/len(y)))
    X_train,X_test,y_train,y_test=train_test_split(X,y,test_size=test_size,random_state=SEED,stratify=y)
    cv_splits=min(5,int(y_train.value_counts().min()))
    if cv_splits<2:raise ValueError("Not enough training samples per class for stratified cross-validation.")
    models={"logistic_regression":LogisticRegression(class_weight="balanced",max_iter=2000,random_state=SEED)}
    if comparison=="random_forest":models["random_forest"]=RandomForestClassifier(n_estimators=250,class_weight="balanced",random_state=SEED,min_samples_leaf=2)
    elif comparison=="gradient_boosting":
        from sklearn.ensemble import HistGradientBoostingClassifier
        models["gradient_boosting"]=HistGradientBoostingClassifier(random_state=SEED)
    else:raise ValueError("Comparison model must be random_forest or gradient_boosting.")
    output_dir=Path(output_dir);output_dir.mkdir(parents=True,exist_ok=True)
    results={};fitted={}
    cv=StratifiedKFold(n_splits=cv_splits,shuffle=True,random_state=SEED)
    for name,estimator in models.items():
        pipe=Pipeline([("features",clone(pre)),("model",estimator)])
        scoring={"precision":"precision","recall":"recall","f1":"f1","roc_auc":"roc_auc"}
        cv_results=cross_validate(pipe,X_train,y_train,cv=cv,scoring=scoring,error_score="raise")
        pipe.fit(X_train,y_train)
        pred=pipe.predict(X_test);proba=pipe.predict_proba(X_test)[:,1]
        metrics=_metrics(y_test,pred,proba)
        metrics["cross_validation"]={k:float(np.mean(v)) for k,v in cv_results.items() if k.startswith("test_")}
        metrics["held_out_size"]=len(y_test);results[name]=metrics;fitted[name]=pipe
    # The comparison baseline uses both attendance and available academic result percentages.
    if "attendance_pct" in X_test.columns and academic_features:
        attendance=pd.to_numeric(X_test["attendance_pct"],errors="coerce")
        academic=X_test[academic_features].apply(pd.to_numeric,errors="coerce").mean(axis=1)
        baseline_pred=np.array([
            rule_baseline(att, result, attendance_threshold, ACADEMIC_THRESHOLD)
            for att, result in zip(attendance,academic)
        ])
        baseline_proba=baseline_pred.astype(float)
        results["attendance_baseline"]=_metrics(y_test,baseline_pred,baseline_proba)
    else:
        results["attendance_baseline"]={"available":False,"reason":"Both attendance_pct and an academic result percentage are required."}
    primary=results["logistic_regression"]
    baseline=results["attendance_baseline"]
    model_beats_baseline=bool(baseline.get("available",True) is not False and
        primary["recall"]>=baseline.get("recall",0) and primary["f1"]>baseline.get("f1",0))
    # Baseline remains the active production rule unless model improves F1 without sacrificing at-risk recall.
    eligible=model_beats_baseline
    version="stage5-"+datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")  # microseconds: two runs in one second must not collide on UNIQUE(version)
    active_name="logistic_regression" if eligible else "attendance_baseline"
    active_path=None;model_sha=None
    if eligible:
        active_path=output_dir/f"{version}_logistic_regression.pkl"
        with active_path.open("wb") as f:pickle.dump({"pipeline":fitted["logistic_regression"],"features":features,"version":version},f)
        model_sha=hashlib.sha256(active_path.read_bytes()).hexdigest()
    report={"version":version,"dataset_kind":dataset_kind,"training_rows":len(X_train),"test_rows":len(X_test),
            "features":features,"target":TARGET,"seed":SEED,"comparison_model":comparison,"models":results,
            "active_source":active_name,"model_outperforms_baseline":model_beats_baseline,"model_sha256":model_sha,
            "note":"Synthetic demonstration metrics are not real-world accuracy." if dataset_kind=="synthetic_demo" else
                   "Validate on representative, anonymized historical data before operational use."}
    (output_dir/f"{version}_evaluation.json").write_text(json.dumps(report,indent=2,allow_nan=False))
    return {"report":report,"active_model_path":str(active_path) if active_path else None,"active_source":active_name,
            "fitted_models":fitted}

def predict_frame(model_path, frame, expected_sha256=None):
    """Load a trained model and score ``frame``.
    pickle executes code on load, so the file's SHA-256 must match the digest recorded when it was
    trained; a swapped or edited file is refused instead of unpickled."""
    raw=Path(model_path).read_bytes()
    if not expected_sha256 or hashlib.sha256(raw).hexdigest()!=expected_sha256:
        raise ValueError("Model file failed its integrity check; retrain the model before generating predictions.")
    bundle=pickle.loads(raw)
    features=bundle["features"];validate_feature_columns(features)
    missing=set(features)-set(frame.columns)
    if missing:raise ValueError("Prediction data missing features: "+", ".join(sorted(missing)))
    probs=bundle["pipeline"].predict_proba(frame[features])[:,1]
    return [{"score":float(p),"band":band_for_score(p),"factors":_explain(bundle["pipeline"],frame.iloc[i:i+1][features],features)}
            for i,p in enumerate(probs)],bundle["version"]

def _explain(pipeline,row,features):
    # Plain-language directional factors based on the change in predicted probability when a feature is neutralized.
    base=float(pipeline.predict_proba(row)[:,1][0]); impacts=[]
    for feature in features:
        altered=row.copy()
        # "Neutralise" = treat the value as missing, so the pipeline's imputer substitutes the training
        # median / most-frequent value. (Setting it to 0 meant e.g. 0% attendance - an extreme, not neutral.)
        altered[feature]=np.nan
        changed=float(pipeline.predict_proba(altered)[:,1][0])
        # lowers=True means removing this feature's information LOWERS the risk, i.e. the feature was pushing risk UP.
        impacts.append((abs(base-changed),feature,changed<base))
    names={"attendance_pct":"Attendance","subjects_below_threshold":"Subjects below attendance threshold",
           "attendance_trend":"Attendance trend","internal_marks_pct":"Internal/mid-term marks",
           "assessment_trend":"Assessment trend","previous_result_pct":"Previous-semester results",
           "backlog_count":"Previous backlogs","course":"Course","semester":"Semester"}
    impacts.sort(reverse=True)
    return [({"factor":names.get(f,f.replace("_"," ").title()),"direction":"increases estimated risk" if lowers else "reduces estimated risk"})
            for _,f,lowers in impacts[:3]]
