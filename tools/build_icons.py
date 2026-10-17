"""Build static/icons/<name>.svg|png: official product icons for the diagrams (Mermaid `img` nodes keep their colours).

Sources (all official artwork):
- Iconify `logos` set (CC0): AWS service icons and product logos (Claude, Terraform, GitLab, Docker, Kubernetes...).
- AWS Architecture Icons missing from `logos` (Bedrock, Organizations, Control Tower...): the PNGs bundled with the
  `diagrams` package (github.com/mingrammer/diagrams, resources/aws), embedded as 128 px PNG data URIs.
- Kubernetes resource and control-plane icons: github.com/kubernetes/community/icons (Apache-2.0 or CC-BY-4.0).

python tools/build_icons.py   (needs network; re-run after editing the lists below)
"""
import io, json, re, urllib.request
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "static" / "icons"
LOGOS = ["aws-api-gateway", "aws-athena", "aws-aurora", "aws-certificate-manager", "aws-cloudformation", "aws-cloudfront",
         "aws-cloudtrail", "aws-cloudwatch", "aws-codepipeline", "aws-cognito", "aws-config", "aws-dynamodb", "aws-ec2",
         "aws-ecs", "aws-eks", "aws-elb", "aws-eventbridge", "aws-fargate", "aws-glacier", "aws-glue", "aws-iam",
         "aws-kinesis", "aws-kms", "aws-lake-formation", "aws-lambda", "aws-rds", "aws-route53", "aws-s3",
         "aws-secrets-manager", "aws-sns", "aws-sqs", "aws-step-functions", "aws-systems-manager", "aws-vpc", "aws-waf",
         "aws-xray", "aws", "anthropic-icon", "claude-icon", "docker-icon", "git-icon", "github-icon", "gitlab", "grafana",
         "hashicorp-icon", "helm", "kubernetes", "nginx", "openai-icon", "opensearch", "postgresql", "prometheus", "python",
         "slack-icon", "terraform-icon", "vault-icon"]
AWS_PNG = {"aws-bedrock": "ml/bedrock", "aws-sagemaker": "ml/sagemaker", "aws-organizations": "management/organizations",
           "aws-control-tower": "management/control-tower", "aws-macie": "security/macie",
           "aws-guardduty": "security/guardduty", "aws-security-hub": "security/security-hub",
           "aws-inspector": "security/inspector", "aws-transit-gateway": "network/transit-gateway",
           "aws-direct-connect": "network/direct-connect", "aws-ecr": "compute/ec2-container-registry"}
K8S = {f"k8s-{n}": f"resources/unlabeled/{n}" for n in
       ["pod", "deploy", "rs", "svc", "pvc", "pv", "sc", "cm", "secret", "ing", "hpa", "netpol", "job", "cronjob", "ns",
        "ep", "ds", "sts", "sa", "role", "rb", "c-role", "crb", "vol"]}
K8S |= {f"k8s-{n}": f"control_plane_components/labeled/{n}" for n in ["api", "kubelet", "k-proxy", "sched", "c-m"]}
K8S |= {f"k8s-{n}": f"infrastructure_components/unlabeled/{n}" for n in ["etcd", "node", "control-plane"]}


def get(url: str) -> bytes:
    return urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "exam-portal-icons"}), timeout=60).read()


def svg_icon(svg: str, uid: str) -> dict:
    vb = re.search(r'viewBox="([\d.\s-]+)"', svg)
    x, y, w, h = (float(v) for v in vb.group(1).split()) if vb else (0, 0, 100, 100)
    body = re.sub(r"(?s)^.*?<svg[^>]*>|</svg>\s*$", "", svg)
    body = re.sub(r"(?s)<metadata.*?</metadata>|<sodipodi:[^>]*/>|<sodipodi:.*?</sodipodi:[^>]*>|<!--.*?-->", "", body)
    body = re.sub(r'\s(?:inkscape|sodipodi):[\w-]+="[^"]*"', "", body)
    body = re.sub(r'id="([^"]+)"', lambda m: f'id="{uid}-{m.group(1)}"', body)        # ids unique across icons
    body = re.sub(r"url\(#([^)]+)\)", lambda m: f"url(#{uid}-{m.group(1)})", body)
    body = re.sub(r'href="#([^"]+)"', lambda m: f'href="#{uid}-{m.group(1)}"', body)
    if x or y:
        body = f'<g transform="translate({-x} {-y})">{body}</g>'
    return {"body": body.strip(), "width": w, "height": h}


def png_icon(png: bytes) -> dict:
    try:                                   # shrink to 128 px when Pillow is around; the icons draw at ~48 px
        from PIL import Image
        im = Image.open(io.BytesIO(png)); im.thumbnail((128, 128))
        buf = io.BytesIO(); im.save(buf, "PNG", optimize=True); png = buf.getvalue()
    except ImportError:
        pass
    return {"png": png}


icons = {}
logos = json.loads(get("https://api.iconify.design/logos.json?icons=" + ",".join(LOGOS)))
for name, ic in logos["icons"].items():
    icons[name] = {"body": ic["body"], "width": ic.get("width", logos.get("width", 256)), "height": ic.get("height", logos.get("height", 256))}
missing = logos.get("not_found", [])
for name, rel in AWS_PNG.items():
    icons[name] = png_icon(get(f"https://raw.githubusercontent.com/mingrammer/diagrams/master/resources/aws/{rel}.png"))
for name, rel in K8S.items():
    icons[name] = svg_icon(get(f"https://raw.githubusercontent.com/kubernetes/community/master/icons/svg/{rel}.svg").decode("utf-8"), name)
OUT.mkdir(parents=True, exist_ok=True)
for name, ic in icons.items():
    if "png" in ic:
        (OUT / f"{name}.png").write_bytes(ic["png"])
    else:
        (OUT / f"{name}.svg").write_text(f'<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" '
                                         f'viewBox="0 0 {ic["width"]} {ic["height"]}">{ic["body"]}</svg>', encoding="utf-8")
size = sum(f.stat().st_size for f in OUT.iterdir()) // 1024
print(f"{len(icons)} icons, {size} KB -> {OUT}; not in logos: {missing}")
