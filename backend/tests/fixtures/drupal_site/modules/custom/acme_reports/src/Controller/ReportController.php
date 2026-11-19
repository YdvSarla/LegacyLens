<?php

namespace Drupal\acme_reports\Controller;

use Drupal\acme_reports\Service\ReportBuilder;
use Drupal\Core\Controller\ControllerBase;

class ReportController extends ControllerBase {

  public function __construct(protected ReportBuilder $builder) {}

  public function list() {
    return ['#markup' => $this->builder->build()];
  }

}
