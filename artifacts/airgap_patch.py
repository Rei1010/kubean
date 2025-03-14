import os
import re
import shutil
import subprocess
import urllib.request
import sys
import yaml
import json
from datetime import datetime
from pathlib import Path
from shutil import which

# Copyright 2023 Authors of kubean-io
# SPDX-License-Identifier: Apache-2.0

CUR_DIR = os.getcwd()
KUBEAN_TAG = "airgap_patch"
MODE = os.getenv("MODE", default="INCR")  ## INCR or FULL
ZONE = os.getenv("ZONE", default="DEFAULT")  ## DEFAULT or CN
OPTION = os.getenv("OPTION", default="all")  # all create_files create_images
SPRAY_RELEASE = os.getenv("SPRAY_RELEASE")
SPRAY_COMMIT = os.getenv("SPRAY_COMMIT")

SPRAY_REPO_PATH = os.path.join(CUR_DIR, "kubespray")

OFFLINE_TMP_REL_PATH = "contrib/offline/temp"
OFFLINE_TMP_ABS_PATH = os.path.join(SPRAY_REPO_PATH, OFFLINE_TMP_REL_PATH)
OFFLINE_VER_CR_TEMP = os.getenv("OFFLINEVERSION_CR_TEMPLATE",
                                default=os.path.join(CUR_DIR,
                                                     "artifacts/template/localartifactset.template.yml"))
KEYWORDS = {
    "kube_version": ["kubelet", "kubectl", "kubeadm", "kube-apiserver", "kube-controller-manager", "kube-scheduler", "kube-proxy",
                     "pause", "coredns", "crictl", "cri-o"],
    "cni_version": ["cni"],
    "containerd_version": ['containerd'],
    "calico_version": ['calico'],
    "cilium_version": ['cilium'],
    "etcd_version": ['etcd'],
    "pod_infra_version": ['pause'],
    "runc_version": ['runc'],
}

def file_lines_to_list(filename):
    with open(filename) as file:
        return [line.rstrip() for line in file]

def get_list_include_keywords(list, *keywords):
    result = []
    for line in list:
        for keyword in keywords:
            if keyword in line:
                result.append(line.strip())
    return result

def check_dependencies():
    if not os.path.exists(SPRAY_REPO_PATH):
        print("kubespray repo path not found")
        sys.exit(1)
    if which("skopeo") is None:
        print("skopeo command not found")
        sys.exit(1)

def get_manifest_version(key, manifest_dict):
    result = []
    value = manifest_dict.get(key, [])
    if isinstance(value, str):
        result.append(value.strip())
    if isinstance(value, list):
        for v in value:
            result.append(str(v).strip())
    return list(set(result))

def execute_gen_airgap_pkgs(arg_option, arch):
    if not os.path.exists("artifacts/gen_airgap_pkgs.sh"):
        print("gen_airgap_pkgs.sh not found in artifacts")
        sys.exit(1)
    if subprocess.run(["bash", "artifacts/gen_airgap_pkgs.sh", "offline_dir"],
                      env={"KUBEAN_TAG": KUBEAN_TAG, "ARCH": arch, "ZONE": ZONE}).returncode != 0:
        print("execute gen_airgap_pkgs.sh but failed")
        sys.exit(1)
    if subprocess.run(["bash", "artifacts/gen_airgap_pkgs.sh", str(arg_option)],
                      env={"KUBEAN_TAG": KUBEAN_TAG, "ARCH": arch, "ZONE": ZONE}).returncode != 0:
        print("execute gen_airgap_pkgs.sh but failed")
        sys.exit(1)

def create_files(file_urls, arch):
    os.chdir(CUR_DIR)
    with open(os.path.join(OFFLINE_TMP_ABS_PATH, "files.list"), "w") as f:
        f.write("\n".join(file_urls))
        f.flush()
    execute_gen_airgap_pkgs("files", arch)

def create_images(image_urls, arch):
    os.chdir(CUR_DIR)
    with open(os.path.join(OFFLINE_TMP_ABS_PATH, "images.list"), "w") as f:
        f.write("\n".join(image_urls))
        f.flush()
    execute_gen_airgap_pkgs("images", arch)

def create_localartifactset_cr(manifest_data):
    os.chdir(CUR_DIR)
    if not os.path.exists(OFFLINE_VER_CR_TEMP):
        print("not found kubeanofflineversion template")
        sys.exit(1)
    template_file = open(OFFLINE_VER_CR_TEMP)
    offlineversion_cr_dict = yaml.load(template_file, Loader=yaml.loader.FullLoader)  # dict
    template_file.close()
    offlineversion_cr_dict["spec"]["docker"] = []
    offlineversion_cr_dict["metadata"]["labels"] = {}
    if SPRAY_RELEASE != "":
        offlineversion_cr_dict["metadata"]["name"] = f"localartifactset-{SPRAY_RELEASE}-{SPRAY_COMMIT}-{int(datetime.now().timestamp())}"
        offlineversion_cr_dict["metadata"]["labels"]["kubean.io/sprayRelease"] = SPRAY_RELEASE
    else:
        offlineversion_cr_dict["metadata"]["name"] = f"localartifactset-patch-{int(datetime.now().timestamp())}"
        offlineversion_cr_dict["metadata"]["labels"]["kubean.io/sprayRelease"] = "master"
    items_array = offlineversion_cr_dict["spec"]["items"]

    for item in items_array:
        item_name = item.get("name", "")
        manifest_keys = [ key for key in KEYWORDS]
        for version_key in manifest_keys:
            component_name = re.split('_', version_key)[0]
            if item_name == component_name:
                versions = manifest_data.get(version_key)
                if MODE == "FULL" and component_name != 'kube' and versions is None:
                    versions = ['default']
                if isinstance(versions, list):
                    item["versionRange"] = versions
                if isinstance(versions, str):
                    item['versionRange'] = [versions]

    offlineversion_cr_dict["spec"]["items"] = items_array
    kubeanofflineversion_file = open(
        os.path.join(KUBEAN_TAG, "localartifactset.cr.yaml"),
        "w",
        encoding="utf-8")
    yaml.dump(offlineversion_cr_dict, kubeanofflineversion_file)
    kubeanofflineversion_file.close()

def get_manifest_data():
    manifest_yml_file = os.getenv("MANIFEST_CONF", default="manifest.yml")
    if (not os.path.exists(manifest_yml_file)) or (Path(manifest_yml_file).read_text().replace("\n", "").strip() == ""):
        print("manifest yaml file does not exist or empty.")
        sys.exit(1)
    with open(manifest_yml_file, "r") as stream:
        return yaml.safe_load(stream)

def get_other_required_keywords(manifest_dict):
    other_required_keywords = [
        "crun", "runsc", "cri-dockerd", "yq", "nginx", "k8s-dns-node-cache", "cluster-proportional-autoscaler"]
    manifest_keys = [ key for key in manifest_dict]
    keys_range = [ key for key in KEYWORDS]
    list_diff = list(set(keys_range) - set(manifest_keys))
    print(f'- keys_range: {keys_range}\n- manifest_keys: {manifest_keys}\n- list_diff: {list_diff}\n')
    for key in list_diff:
        other_required_keywords += KEYWORDS[key]
    return other_required_keywords

def get_pod_infra_versions(kube_versions):
    pod_infra_versions = []
    depend_url_templ = "https://raw.githubusercontent.com/kubernetes/kubernetes/{}/build/dependencies.yaml"
    if ZONE == "CN":
        depend_url_templ = "https://gitee.com/mirrors/kubernetes/raw/{}/build/dependencies.yaml"
    for kube_version in list(kube_versions):
        dependencies_url = depend_url_templ.format(kube_version)
        print(f'- dependencies url: {dependencies_url}')
        f = urllib.request.urlopen(dependencies_url)
        response_content = f.read().decode('utf-8')
        
        yaml_obj = yaml.safe_load(response_content)
        for item in yaml_obj.get("dependencies"):
            if item.get("name") in ["k8s.gcr.io/pause", "registry.k8s.io/pause"]:
                pod_infra_versions.append(item.get('version'))
    return pod_infra_versions

def build_jobs_params(manifest_dict):
    manifest_dict["pod_infra_version"] = get_pod_infra_versions(manifest_dict.get("kube_version", []))
    print(f'- manifest_dict: {manifest_dict}\n')
    max_len = max(len(item) for _, item in manifest_dict.items() if isinstance(item, list))
    other_required_keywords = get_other_required_keywords(manifest_dict)
    jobs_params = {
        "arch": manifest_dict.get('image_arch', ['amd64']),
        "jobs": [{"keywords": [], "extra_vars": [],} for i in range(max_len)],
        "other_keywords": other_required_keywords,
    }
    manifest_keys=['image_arch']
    manifest_keys += [ key for key in KEYWORDS]
    for index, job in enumerate(jobs_params.get('jobs', [])):
        for component, versions in manifest_dict.items():
            if component not in manifest_keys:
                print(f"unknown component version key: {component}")
                sys.exit(1)
            if isinstance(versions, str) and index == 0 and component != "image_arch":
                job['keywords'] += KEYWORDS.get(component, [])
                job['extra_vars'].append(f"{component}='{versions}'")
            if isinstance(versions, list) and index < len(versions) and component != "image_arch":
                job['keywords'] += KEYWORDS.get(component, [])
                job['extra_vars'].append(f"{component}='{versions[index]}'")
    print(f'- jobs_params: {json.dumps(jobs_params, indent=2)}\n')
    return jobs_params

def gen_airgap_packages(option, arch, bin_urls, img_urls):
    if option == "all":
        create_files(bin_urls, arch=arch)
        create_images(img_urls, arch=arch)
        execute_gen_airgap_pkgs("copy_import_sh", arch=arch)
    if option == "create_files":
        create_files(bin_urls, arch=arch)
    if option == "create_images":
        create_images(img_urls, arch=arch)

def batch_gen_airgap_resources(jobs_params):
    other_required_list = {key: [] for key in ['file_list', 'image_list']}
    list_data = {key: [] for key in ['file_list', 'image_list']}
    is_executed = False
    for arch in jobs_params.get('arch', []):
        for job in jobs_params.get('jobs',[]):
            extra_vars_cmd = []
            for var in job.get('extra_vars'):
                extra_vars_cmd.extend(["-e", var])
            os.chdir(SPRAY_REPO_PATH)
            if os.path.exists(f"{OFFLINE_TMP_REL_PATH}"):
                shutil.rmtree(f"{OFFLINE_TMP_REL_PATH}")
            cmd = ["bash", "contrib/offline/generate_list.sh", "-e", f"image_arch='{arch}'"]
            cmd += extra_vars_cmd
            print(f"\n- cmd: {cmd}\n")
            result = subprocess.run(cmd, capture_output=True, text=True)
            if result.returncode != 0:
                print(result.stdout)
                print(result.stderr)
                sys.exit(1)
            if not os.path.exists(f"{OFFLINE_TMP_REL_PATH}/images.list"):
                print(f"not found '{OFFLINE_TMP_REL_PATH}/images.list'")
                sys.exit(1)
            if not os.path.exists(f"{OFFLINE_TMP_REL_PATH}/files.list"):
                print(f"not found '{OFFLINE_TMP_REL_PATH}/files.list'")
                sys.exit(1)

            files_list = file_lines_to_list(f"{OFFLINE_TMP_REL_PATH}/files.list")
            images_list = file_lines_to_list(f"{OFFLINE_TMP_REL_PATH}/images.list")
            file_urls, image_urls = get_list_include_keywords(files_list, *job.get('keywords')), get_list_include_keywords(images_list, *job.get('keywords'))
            if MODE == "FULL" and not is_executed:
                other_required_keywords = jobs_params.get('other_keywords', [])
                other_required_list['file_list'] = get_list_include_keywords(files_list, *other_required_keywords)
                other_required_list['image_list'] = get_list_include_keywords(images_list, *other_required_keywords)
            is_executed = True

            os.chdir(CUR_DIR)
            list_data['file_list'] += file_urls
            list_data['image_list'] += image_urls

        list_data['file_list'] += other_required_list['file_list']
        list_data['image_list'] += other_required_list['image_list']
        list_data['file_list'], list_data['image_list'] = list(set(list_data['file_list'])), list(set(list_data['image_list']))
        print_list(list_data['file_list'],  list_data['image_list'])
        gen_airgap_packages(OPTION, arch, list_data['file_list'], list_data['image_list'])

def print_list(file_list, image_list):
    print("---------------- file urls -----------------\n")
    for file_url in file_list:
        print(f'* {file_url}\n')
    print("---------------- image urls -----------------\n")
    for image_url in image_list:
        print(f'* {image_url}\n')

if __name__ == '__main__':
    print(f"OPTION:{OPTION}, ZONE: {ZONE}, MODE: {MODE}\n")
    check_dependencies()
    manifest_data = get_manifest_data()
    batch_gen_airgap_resources(build_jobs_params(manifest_data))
    create_localartifactset_cr(manifest_data)
